import hashlib
import json
import logging
from typing import Optional
import uuid

from decimal import ROUND_HALF_UP, Decimal
from django.core.mail import send_mail
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import PaystackTransaction, Profile, SavingsGroup, Wallet
from .paystack import (
    PaystackError,
    create_transfer_recipient,
    generate_cashout_reference,
    generate_topup_reference,
    initialize_transaction,
    verify_transaction,
    verify_transfer,
    verify_webhook_signature,
)
from .services import LedgerService, NotificationService
from .utils import idempotent_request

logger = logging.getLogger("accounts.finance")

# Helpers

MIN_TOPUP_GHS = Decimal("1.00")
MAX_TOPUP_GHS = Decimal("50000.00")
MIN_CASHOUT_GHS = Decimal("5.00")
CASHOUT_FEE_PERCENT = Decimal("8.00")


def _get_or_create_paystack_recipient(profile: Profile) -> str:
    """
    Return the cached Paystack recipient_code for a profile, creating it
    on Paystack if it doesn't exist yet.
    """
    if profile.paystack_recipient_code:
        return profile.paystack_recipient_code

    # Decrypt the encrypted momo_number field to get the plain number
    momo_number = str(profile.momo_number)
    recipient_code = create_transfer_recipient(
        name=profile.momo_name,
        account_number=momo_number,
        momo_provider=profile.momo_provider,
    )

    profile.paystack_recipient_code = recipient_code
    profile.save(update_fields=["paystack_recipient_code"])
    return recipient_code


# View: Initialize Top-Up

@extend_schema(
    request=inline_serializer(
        name="TopUpInitializeRequest",
        fields={
            "amount": serializers.DecimalField(
                max_digits=10,
                decimal_places=2,
                help_text=f"Amount in GHS (min ₵{MIN_TOPUP_GHS}, max ₵{MAX_TOPUP_GHS})",
            ),
        },
    ),
    responses={
        200: inline_serializer(
            name="TopUpInitializeResponse",
            fields={
                "authorization_url": serializers.CharField(),
                "access_code": serializers.CharField(),
                "reference": serializers.CharField(),
            },
        ),
        400: {"description": "Invalid amount"},
    },
    description=(
        "Initialize a Paystack transaction for topping up the user's wallet. "
        "The frontend uses the returned `access_code` with Paystack Popup JS "
        "(`popup.resumeTransaction(access_code)`) to show the payment sheet. "
        "The wallet is credited ONLY after the `charge.success` webhook is received and verified."
    ),
    tags=["Payments"],
)
class TopUpInitializeView(APIView):
    """
    Step 1 of wallet top-up:
      1. Validate amount
      2. Create a PaystackTransaction record (status=pending)
      3. Call Paystack initialize endpoint
      4. Return access_code + authorization_url to frontend
    """
    permission_classes = [IsAuthenticated]

    @idempotent_request
    def post(self, request):
        raw_amount = request.data.get("amount")
        if raw_amount is None:
            return Response(
                {"error": "amount is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            amount = Decimal(str(raw_amount))
        except Exception:
            return Response(
                {"error": "amount must be a valid number."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if amount < MIN_TOPUP_GHS:
            return Response(
                {"error": f"Minimum top-up amount is ₵{MIN_TOPUP_GHS}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if amount > MAX_TOPUP_GHS:
            return Response(
                {"error": f"Maximum top-up amount is ₵{MAX_TOPUP_GHS}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = request.user
        reference = generate_topup_reference(user.id)

        # Persist intent BEFORE calling Paystack so we can reconcile later
        PaystackTransaction.objects.create(
            user=user,
            reference=reference,
            transaction_type="topup",
            amount=amount,
            status="pending",
        )

        try:
            data = initialize_transaction(
                email=user.email,
                amount_ghs=amount,
                reference=reference,
                metadata={
                    "user_id": user.id,
                    "transaction_type": "topup",
                    "request_id": getattr(request, "request_id", None),
                },
            )
        except PaystackError as exc:
            logger.error(
                {
                    "event": "topup_initialize_failed",
                    "user_id": user.id,
                    "amount": str(amount),
                    "error": str(exc),
                }
            )
            # Mark the intent as failed so we don't have orphaned pending rows
            PaystackTransaction.objects.filter(reference=reference).update(
                status="failed"
            )
            return Response(
                {"error": "Could not connect to payment provider. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        logger.info(
            {
                "event": "topup_initialized",
                "user_id": user.id,
                "amount": str(amount),
                "reference": reference,
                "request_id": getattr(request, "request_id", None),
            }
        )

        return Response(
            {
                "authorization_url": data["authorization_url"],
                "access_code": data["access_code"],
                "reference": reference,
            },
            status=status.HTTP_200_OK,
        )


# View: Paystack Webhook

PAYSTACK_IPS = frozenset({"52.31.139.75", "52.49.173.169", "52.214.14.220"})

@extend_schema(exclude=True)
class PaystackWebhookView(APIView):
    """
    Receives all Paystack webhook events.

    Security:
    - Verifies HMAC-SHA512 signature on every request.
    - Always returns 200 quickly so Paystack doesn't retry.
    - All heavy work runs inside atomic transactions.

    Handled events:
    - charge.success     → credit user wallet (top-up)
    - transfer.success   → mark payout as disbursed
    - transfer.failed    → mark payout as failed, notify admin
    - transfer.reversed  → mark payout as reversed, notify admin
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    @csrf_exempt
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def post(self, request):
        # ALWAYS verify HMAC first
        signature = request.headers.get("x-paystack-signature", "")

        if not verify_webhook_signature(request.body, signature):
            logger.warning({
                "event": "webhook_invalid_signature",
                "ip": request.META.get("REMOTE_ADDR"),
                "env": getattr(settings, "DJANGO_ENV", "unknown"),
            })
            # Always return 200
            return Response({"status": "ignored"}, status=200)

        # Optional IP allowlist (production only, defence-in-depth) ─
        django_env = getattr(settings, "DJANGO_ENV", "development")
        if django_env == "production":
            client_ip = (
                request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
                or request.META.get("REMOTE_ADDR", "")
            )
            if client_ip not in PAYSTACK_IPS:
                logger.warning({
                    "event": "webhook_ip_rejected",
                    "ip": client_ip,
                    "note": "HMAC was valid but IP not in allowlist",
                })

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            logger.warning({"event": "webhook_bad_json"})
            return Response({"status": "bad_json"}, status=200)

        event = payload.get("event", "")
        data = payload.get("data", {})

        logger.info({"event": "paystack_webhook_received", "paystack_event": event})

        try:
            if event == "charge.success":
                self._handle_charge_success(data)
            elif event == "transfer.success":
                self._handle_transfer_success(data)
            elif event == "transfer.failed":
                self._handle_transfer_failed(data)
            elif event == "transfer.reversed":
                self._handle_transfer_reversed(data)
            else:
                logger.info({"event": "webhook_unhandled", "paystack_event": event})
        except Exception as exc:
            logger.critical({
                "event": "webhook_handler_exception",
                "paystack_event": event,
                "error": str(exc),
            })

        return Response({"status": "ok"}, status=200)


    @transaction.atomic
    def _handle_charge_success(self, data: dict):
        reference = data.get("reference", "")

        # Find the PaystackTransaction created at initialize time
        try:
            ps_tx = PaystackTransaction.objects.select_for_update().get(
                reference=reference,
                transaction_type="topup",
            )
        except PaystackTransaction.DoesNotExist:
            logger.error(
                {
                    "event": "topup_webhook_no_matching_transaction",
                    "reference": reference,
                }
            )
            return

        # Idempotency guard: don't double-credit
        if ps_tx.status == "success":
            logger.warning(
                {
                    "event": "topup_webhook_duplicate",
                    "reference": reference,
                }
            )
            return

        # Verify on Paystack's side (defense-in-depth)
        try:
            verified = verify_transaction(reference)
        except PaystackError as exc:
            logger.error(
                {
                    "event": "topup_verify_failed",
                    "reference": reference,
                    "error": str(exc),
                }
            )
            return

        # Amount mismatch check
        verified_pesewas = int(verified.get("amount", 0))
        expected_pesewas = int(
            (ps_tx.amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )

        if verified_pesewas != expected_pesewas:
            logger.critical({
                "event": "topup_amount_mismatch",
                "reference": reference,
                "expected_pesewas": expected_pesewas,
                "received_pesewas": verified_pesewas,
                "user_id": ps_tx.user_id,
                "intended_amount": str(ps_tx.amount),
            })

            ps_tx.status = "failed"
            ps_tx.save(update_fields=["status", "updated_at"])

            # Alert admins immediately
            self._send_topup_amount_mismatch_alert(
                ps_tx, verified_pesewas, expected_pesewas
            )
            return

        # Credit the wallet (only if amounts match)
        LedgerService.create_entry(
            user=ps_tx.user,
            actor=ps_tx.user,
            amount=ps_tx.amount,
            direction="credit",
            transaction_type="deposit",
            description=f"Wallet top-up via {verified.get('channel', 'Paystack')}",
            reference=f"TOPUP-{reference}",
            request_id=None,
        )

        ps_tx.status = "success"
        ps_tx.paystack_reference = verified.get("id", "")
        ps_tx.save(update_fields=["status", "paystack_reference", "updated_at"])

        # In-app notification
        try:
            NotificationService._create(
                user=ps_tx.user,
                notification_type="system",
                title="Wallet Top-Up Successful 💰",
                message=f"₵{ps_tx.amount:,.2f} has been added to your SnappX wallet.",
                action_url="Dashboard",
                metadata={"amount": str(ps_tx.amount), "reference": reference},
            )
        except Exception as exc:
            logger.error(f"[Webhook] Notification error after topup: {exc}")

        logger.info(
            {
                "event": "wallet_topped_up",
                "user_id": ps_tx.user_id,
                "amount": str(ps_tx.amount),
                "reference": reference,
            }
        )

    # transfer.success

    @transaction.atomic
    def _handle_transfer_success(self, data: dict):
        reference = data.get("reference", "")
        transfer_code = data.get("transfer_code", "")

        try:
            ps_tx = PaystackTransaction.objects.select_for_update().get(
                reference=reference,
                transaction_type__in=["payout", "cashout"],
            )
        except PaystackTransaction.DoesNotExist:
            logger.error(
                {
                    "event": "transfer_webhook_no_matching_transaction",
                    "reference": reference,
                }
            )
            return

        if ps_tx.status == "success":
            logger.warning(
                {"event": "transfer_webhook_duplicate", "reference": reference}
            )
            return

        ps_tx.status = "success"
        ps_tx.paystack_reference = transfer_code
        ps_tx.save(update_fields=["status", "paystack_reference", "updated_at"])

        # Notify the user their money is on its way
        try:
            amount_ghs = ps_tx.amount
            NotificationService._create(
                user=ps_tx.user,
                notification_type="payout_received",
                title="Transfer Successful 🎉",
                message=(
                    f"₵{amount_ghs:,.2f} has been sent to your MoMo account. "
                    "It should arrive within a few minutes."
                ),
                action_url="Dashboard",
                metadata={"amount": str(amount_ghs), "reference": reference},
            )
        except Exception as exc:
            logger.error(f"[Webhook] Notification error after transfer.success: {exc}")

        logger.info(
            {
                "event": "paystack_transfer_success",
                "reference": reference,
                "transfer_code": transfer_code,
                "user_id": ps_tx.user_id,
            }
        )

    # transfer failed

    @transaction.atomic
    def _handle_transfer_failed(self, data: dict):
        reference = data.get("reference", "")
        transfer_code = data.get("transfer_code", "")
        failures = data.get("failures") or []

        try:
            ps_tx = PaystackTransaction.objects.select_for_update().get(
                reference=reference,
                transaction_type__in=["payout", "cashout"],
            )
        except PaystackTransaction.DoesNotExist:
            logger.error(
                {
                    "event": "transfer_failed_webhook_no_match",
                    "reference": reference,
                }
            )
            return

        ps_tx.status = "failed"
        ps_tx.paystack_reference = transfer_code
        ps_tx.failure_reason = str(failures)
        ps_tx.save(update_fields=["status", "paystack_reference", "failure_reason", "updated_at"])

        logger.critical(
            {
                "event": "paystack_transfer_failed",
                "reference": reference,
                "user_id": ps_tx.user_id,
                "failures": failures,
            }
        )

        # Notify user
        try:
            NotificationService._create(
                user=ps_tx.user,
                notification_type="system",
                title="Transfer Failed ⚠️",
                message=(
                    "We could not complete your transfer. "
                    "Our team has been notified and will resolve this. "
                    "Your funds are safe."
                ),
                action_url="Dashboard",
                metadata={"reference": reference},
            )
        except Exception as exc:
            logger.error(f"[Webhook] Notification error after transfer.failed: {exc}")

    # transfer reversed

    @transaction.atomic
    def _handle_transfer_reversed(self, data: dict):
        """
        When Paystack reverses a transfer, they credit our Paystack balance back.
        We need to credit the user's internal wallet too (for cashouts),
        or flag the group payout for retry.
        """
        reference = data.get("reference", "")
        transfer_code = data.get("transfer_code", "")

        try:
            ps_tx = PaystackTransaction.objects.select_for_update().get(
                reference=reference,
                transaction_type__in=["payout", "cashout"],
            )
        except PaystackTransaction.DoesNotExist:
            logger.error({"event": "transfer_reversed_webhook_no_match", "reference": reference})
            return

        if ps_tx.status == "reversed":
            return

        ps_tx.status = "reversed"
        ps_tx.paystack_reference = transfer_code
        ps_tx.save(update_fields=["status", "paystack_reference", "updated_at"])

        if ps_tx.transaction_type == "cashout":
            # Refund: money came back to the Paystack balance so credit user wallet
            LedgerService.create_entry(
                user=ps_tx.user, actor=ps_tx.user, amount=ps_tx.amount,
                direction="credit", transaction_type="refund",
                description="Cashout reversal — funds returned to wallet",
                reference=f"REVERSAL-{reference}-{uuid.uuid4().hex[:8]}",
                request_id=None,
            )

        elif ps_tx.transaction_type == "payout":
            group = ps_tx.related_group
            if group:
                # Reverse the ledger
                reversal_ref = f"payout-reversal-{reference}-{uuid.uuid4().hex[:8]}"
                LedgerService.create_entry(
                    user=ps_tx.user, actor=ps_tx.user, amount=ps_tx.amount,
                    direction="debit", transaction_type="refund",
                    description=f"Payout reversal — group {group.group_name}",
                    reference=f"{reversal_ref}-db",
                    request_id=None, related_group=group,
                )
                LedgerService.create_entry(
                    group=group, actor=group.admin, amount=ps_tx.amount,
                    direction="credit", transaction_type="refund",
                    description="Payout reversal — returned to pot",
                    reference=f"{reversal_ref}-cr",
                    request_id=None, related_group=group,
                )

                # Roll the cycle back so this member gets retried.
                with transaction.atomic():
                    locked = SavingsGroup.objects.select_for_update().get(pk=group.pk)
                    if locked.current_cycle_number > 1:
                        locked.current_cycle_number -= 1
                        if locked.status == "completed":
                            locked.status = "active"
                        locked.save(update_fields=["current_cycle_number", "status"])

                logger.critical({
                    "event": "payout_reversed_ledger_and_cycle_corrected",
                    "group_id": group.id, "user_id": ps_tx.user_id,
                    "amount": str(ps_tx.amount), "reference": reference,
                })

        try:
            NotificationService._create(
                user=ps_tx.user, notification_type="system",
                title="Transfer Reversed ⚠️",
                message=(
                    "Your transfer was reversed by the bank. "
                    + ("Your funds have been returned to your wallet."
                    if ps_tx.transaction_type == "cashout"
                    else "Our team has been notified and will retry.")
                ),
                action_url="Dashboard",
                metadata={"reference": reference},
            )
        except Exception as exc:
            logger.error(f"[Webhook] Notification error after transfer.reversed: {exc}")


    def _send_topup_amount_mismatch_alert(
    self,
    ps_tx: PaystackTransaction,
    verified_pesewas: int,
    expected_pesewas: int,
    ):
            """Send urgent email to admins when Paystack returns a different amount than we expected."""
            admin_emails = [email for name, email in getattr(settings, 'ADMINS', [])]
            if not admin_emails:
                admin_emails = [settings.DEFAULT_FROM_EMAIL]

            subject = f"🚨 CRITICAL: Top-Up Amount Mismatch [{ps_tx.reference}]"

            message = f"""
    CRITICAL TOP-UP AMOUNT MISMATCH

    Reference: {ps_tx.reference}
    User: {ps_tx.user.email} (ID: {ps_tx.user_id})
    Intended amount: ₵{ps_tx.amount:,.2f} ({expected_pesewas} pesewas)
    Paystack returned: {verified_pesewas} pesewas

    The transaction has been marked as FAILED.
    Money is sitting in your Paystack balance but the user's wallet was NOT credited.

    ACTION REQUIRED:
    1. Check Paystack dashboard for transaction {ps_tx.reference}
    2. Manually investigate / refund or credit the user if appropriate.
    3. This should almost never happen — treat as high-priority security incident.

    This alert was triggered automatically by the webhook handler.
    """

            try:
                send_mail(
                    subject=subject,
                    message=message.strip(),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=admin_emails,
                    fail_silently=False,
                )
                logger.info(f"Top-up amount mismatch alert sent to {len(admin_emails)} admins")
            except Exception as email_exc:
                logger.critical(f"Failed to send top-up mismatch alert email: {email_exc}")

# View: Cash Out (user-initiated withdrawal)

@extend_schema(
    request=inline_serializer(
        name="CashOutRequest",
        fields={
            "amount": serializers.DecimalField(
                max_digits=10,
                decimal_places=2,
                help_text=(
                    f"Amount to withdraw in GHS (min ₵{MIN_CASHOUT_GHS}). "
                    f"An {CASHOUT_FEE_PERCENT}% service fee is deducted."
                ),
            ),
        },
    ),
    responses={
        200: inline_serializer(
            name="CashOutResponse",
            fields={
                "message": serializers.CharField(),
                "amount_requested": serializers.DecimalField(max_digits=10, decimal_places=2),
                "service_fee": serializers.DecimalField(max_digits=10, decimal_places=2),
                "amount_to_receive": serializers.DecimalField(max_digits=10, decimal_places=2),
                "reference": serializers.CharField(),
            },
        ),
        400: {"description": "Insufficient funds or invalid amount"},
        403: {"description": "Account not verified"},
        502: {"description": "Could not reach payment provider"},
    },
    description=(
        "User-initiated withdrawal from their SnappX wallet to their registered MoMo account. "
        f"A {CASHOUT_FEE_PERCENT}% service fee is deducted from the requested amount. "
        "The transfer is initiated via Paystack. Final status arrives via webhook."
    ),
    tags=["Payments"],
)
class CashOutView(APIView):
    """
    Cash-out flow:
    """

    permission_classes = [IsAuthenticated]

    @idempotent_request
    def post(self, request):
        # Input validation
        if not request.user.is_verified:
            return Response(
                {"error": "You must be verified to cash out."},
                status=status.HTTP_403_FORBIDDEN,
            )

        raw_amount = request.data.get("amount")
        if raw_amount is None:
            return Response(
                {"error": "amount is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            amount = Decimal(str(raw_amount))
        except Exception:
            return Response(
                {"error": "amount must be a valid number."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if amount < MIN_CASHOUT_GHS:
            return Response(
                {"error": f"Minimum cashout amount is ₵{MIN_CASHOUT_GHS}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if amount > MAX_TOPUP_GHS:
            return Response(
                {"error": f"Maximum cashout amount is ₵{MAX_TOPUP_GHS}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = request.user
        request_id = getattr(request, "request_id", None)
        paystack_reference = generate_cashout_reference(user.id)

        if request_id:
            internal_key = f"cashout-{request_id.lower()}"
        else:
            internal_key = paystack_reference

        # Pre-compute fee and net transfer amount
        service_fee = (amount * CASHOUT_FEE_PERCENT / 100).quantize(Decimal("0.01"))
        amount_to_transfer = amount - service_fee

        #Create PaystackTransaction + debit wallet (single atomic block)
        try:
            with transaction.atomic():
                # Pessimistic lock on wallet to prevent concurrent debits
                wallet = Wallet.objects.select_for_update().get(user=user)

                if wallet.current_balance < amount:
                    return Response(
                        {
                            "error": "Insufficient wallet balance.",
                            "available_balance": str(wallet.current_balance),
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                ps_tx = PaystackTransaction.objects.create(
                    user=user,
                    reference=internal_key,
                    transaction_type="cashout",
                    amount=amount,
                    status="pending",
                )

                # Debit the wallet.
                LedgerService.create_entry(
                    user=user,
                    actor=user,
                    amount=amount,
                    direction="debit",
                    transaction_type="withdrawal",
                    description=f"Cashout request (fee: ₵{service_fee})",
                    reference=f"cashout-ledger-{internal_key}",
                    request_id=request_id,
                )

        except IntegrityError:
            logger.warning({
                "event": "cashout_duplicate_idempotent_request",
                "user_id": user.id,
                "internal_key": internal_key,
                "request_id": request_id,
            })
            return Response(
                {"error": "This cashout request has already been processed."},
                status=status.HTTP_409_CONFLICT,
            )
        except Wallet.DoesNotExist:
            return Response({"error": "Wallet not found."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as exc:
            logger.error({
                "event": "cashout_init_failed",
                "user_id": user.id,
                "internal_key": internal_key,
                "error": str(exc),
            })
            return Response(
                {"error": "Could not process cashout. Please try again."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Call Paystack
        profile = user.profile

        try:
            recipient_code = _get_or_create_paystack_recipient(profile)
        except PaystackError as exc:
            logger.error({
                "event": "cashout_recipient_creation_failed",
                "user_id": user.id,
                "internal_key": internal_key,
                "error": str(exc),
            })
            self._reverse_cashout_debit(user, amount, internal_key, request_id,
                                        reason="Paystack recipient creation failed")
            PaystackTransaction.objects.filter(reference=internal_key).update(
                status="failed",
                failure_reason=f"Recipient creation failed: {exc}"
            )
            return Response(
                {"error": "Could not set up transfer recipient. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        try:
            # Send paystack_reference to Paystack
            transfer_data = _initiate_transfer_with_retry(
                recipient_code=recipient_code,
                amount_ghs=amount_to_transfer,
                reference=paystack_reference,
                reason=f"SnappX Cashout — {profile.full_name}",
            )
        except PaystackError as exc:
            logger.error({
                "event": "cashout_transfer_initiation_failed",
                "user_id": user.id,
                "internal_key": internal_key,
                "paystack_reference": paystack_reference,
                "error": str(exc),
            })
            self._reverse_cashout_debit(user, amount, internal_key, request_id,
                                        reason="Paystack transfer initiation failed")
            PaystackTransaction.objects.filter(reference=internal_key).update(
                status="failed",
                failure_reason=f"Transfer initiation failed: {exc}"
            )
            return Response(
                {"error": "Could not initiate transfer. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Record Paystack's transfer_code on our record
        PaystackTransaction.objects.filter(reference=internal_key).update(
            paystack_reference=transfer_data.get("transfer_code", "") or paystack_reference,
            updated_at=timezone.now(),
        )

        logger.info({
            "event": "cashout_initiated",
            "user_id": user.id,
            "amount": str(amount),
            "fee": str(service_fee),
            "net_transfer": str(amount_to_transfer),
            "internal_key": internal_key,
            "paystack_reference": paystack_reference,
            "transfer_code": transfer_data.get("transfer_code"),
            "request_id": request_id,
        })

        return Response({
            "message": (
                f"Cashout initiated. ₵{amount_to_transfer:,.2f} will be sent "
                f"to your {profile.momo_provider.upper()} MoMo account."
            ),
            "amount_requested": amount,
            "service_fee": service_fee,
            "amount_to_receive": amount_to_transfer,
            "reference": internal_key,
        }, status=status.HTTP_200_OK)


    # Private helper

    def _reverse_cashout_debit(
        self,
        user,
        amount: Decimal,
        original_reference: str,
        request_id: Optional[str],
        reason: str = "Cashout reversal",
    ) -> bool:
        """
        Reverses a cashout debit when Paystack rejected the transfer.
        CRITICAL PATH: If this fails, we send an immediate email to ADMINS
        and notify the user so they are not left in the dark.
        """
        reversal_ref = f"cashout-reversal-{original_reference}-{uuid.uuid4().hex[:8]}"

        try:
            with transaction.atomic():
                LedgerService.create_entry(
                    user=user,
                    actor=user,
                    amount=amount,
                    direction="credit",
                    transaction_type="refund",
                    description=f"Cashout reversal — {reason}",
                    reference=reversal_ref,
                    request_id=request_id,
                )

            logger.info({
                "event": "cashout_debit_reversed",
                "user_id": user.id,
                "amount": str(amount),
                "original_reference": original_reference,
                "reversal_reference": reversal_ref,
            })
            return True

        except Exception as exc:
            # If Money is stuck, alert immediately
            logger.critical({
                "event": "CASHOUT_REVERSAL_FAILED — MANUAL INTERVENTION REQUIRED",
                "user_id": user.id,
                "amount": str(amount),
                "original_reference": original_reference,
                "reversal_reference": reversal_ref,
                "error": str(exc),
            })

            self._send_reversal_failure_alert(user, amount, original_reference, str(exc))

            try:
                NotificationService._create(
                    user=user,
                    notification_type="system",
                    title="Cashout Issue – Action Needed ⚠️",
                    message=(
                        f"Your cashout of ₵{amount:,.2f} could not be completed. "
                        "Your funds are safe in your wallet. Our team has been alerted."
                    ),
                    action_url="Dashboard",
                    metadata={
                        "original_reference": original_reference,
                        "amount": str(amount),
                    },
                )
            except Exception as notify_exc:
                logger.error(f"[CashOut] Failed to notify user about reversal error: {notify_exc}")

            return False

    def _send_reversal_failure_alert(self, user, amount: Decimal, reference: str, error: str):
        """Sends urgent email to ADMINS when cashout reversal fails."""
        admin_emails = [email for name, email in getattr(settings, 'ADMINS', [])]
        if not admin_emails:
            admin_emails = [settings.DEFAULT_FROM_EMAIL]

        subject = f"🚨 CRITICAL: Cashout Reversal Failed – Manual Fix Needed [{reference}]"
        message = f"""
CRITICAL CASH-OUT REVERSAL FAILURE

User: {user.email} (ID: {user.id})
Amount: ₵{amount:,.2f}
Reference: {reference}
Error: {error}

The user's wallet was successfully debited, but:
- The Paystack transfer failed to initiate, AND
- The reversal credit could not be applied.

→ Funds are currently stuck (wallet balance is too low).

ACTION REQUIRED:
Please go to Django Admin → Ledger Entries and manually create a correcting credit entry
for this user with reference "{reference}".

This is a high-priority issue. Please handle immediately.
"""
        try:
            send_mail(
                subject=subject,
                message=message.strip(),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=admin_emails,
                fail_silently=False,
            )
            logger.info(f"Reversal failure alert email sent to {len(admin_emails)} admins")
        except Exception as email_exc:
            logger.critical(f"Failed to send reversal failure email: {email_exc}")

# View: Transaction Status
@extend_schema(
    responses={
        200: inline_serializer(
            name="PaymentStatusResponse",
            fields={
                "reference": serializers.CharField(),
                "status": serializers.CharField(),
                "transaction_type": serializers.CharField(),
                "amount": serializers.DecimalField(max_digits=10, decimal_places=2),
                "created_at": serializers.DateTimeField(),
            },
        ),
        404: {"description": "Transaction not found"},
    },
    description=(
        "Poll the status of a payment transaction by reference. "
        "Prefer webhooks — use this only as a fallback for the frontend."
    ),
    tags=["Payments"],
)
class PaymentStatusView(APIView):
    """
    Polling endpoint so the frontend can check transaction status
    while waiting for the webhook to arrive.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, reference: str):
        try:
            ps_tx = PaystackTransaction.objects.get(
                reference=reference,
                user=request.user,
            )
        except PaystackTransaction.DoesNotExist:
            return Response(
                {"error": "Transaction not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "reference": ps_tx.reference,
                "status": ps_tx.status,
                "transaction_type": ps_tx.transaction_type,
                "amount": ps_tx.amount,
                "created_at": ps_tx.created_at,
            }
        )


# Internal helper — transfer with retry on recipient refresh

def _initiate_transfer_with_retry(
    *,
    recipient_code: str,
    amount_ghs: Decimal,
    reference: str,
    reason: str,
) -> dict:
    """
    Thin wrapper around paystack.initiate_transfer.
    """
    from .paystack import initiate_transfer
    return initiate_transfer(
        recipient_code=recipient_code,
        amount_ghs=amount_ghs,
        reference=reference,
        reason=reason,
    )
