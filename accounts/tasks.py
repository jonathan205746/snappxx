# accounts/tasks.py

import requests
from decimal import Decimal
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.contrib.sites.models import Site
from django.urls import NoReverseMatch, reverse
from django.db import models, transaction
from django.utils import timezone
from datetime import timedelta
from django.db.models import Sum, Value, DecimalField, ExpressionWrapper, Case, When, F
from django.db.models.functions import Coalesce
from django_qstash import shared_task

from accounts.paystack import verify_transaction
from .models import (
    SavingsGroup, Contribution, PayoutOrder, SavingsGoal,
    Wallet, LedgerEntry, IdempotencyKey, GroupJoinRequest
)
from .services import LedgerService, NotificationService
import logging


logger = logging.getLogger('accounts.finance')

DAWUROBO_BASE = "https://devs.sms.api.dawurobo.com/v1/otp"

HEADERS = {
    "accept": "application/json",
    "x-api-key": settings.DAWUROBO_API_KEY,
    "x-access-token": settings.DAWUROBO_ACCESS_TOKEN,
    "Content-Type": "application/json"
}


@shared_task
def send_dawurobo_otp_async(phone_number: str):
    clean_number = phone_number.replace("+", "").replace(" ", "")
    payload = {
        "senderid": settings.DAWUROBO_SENDER_ID,
        "number": clean_number,
        "messagetemplate": "Your SnappX code: %OTPCODE%. Expires in %EXPIRY% min.",
        "expiry": 10,
        "length": 6,
        "type": "NUMERIC"
    }
    try:
        response = requests.post(f"{DAWUROBO_BASE}/generate", json=payload, headers=HEADERS, timeout=15)
        response.raise_for_status()
        return {"success": True}
    except Exception as exc:
        logger.error(f"OTP SEND FAIL: {phone_number} - {exc}")
        raise exc


def verify_and_invalidate_otp_sync(phone_number: str, code: str) -> bool:
    clean_number = phone_number.replace("+", "").replace(" ", "")
    verify_payload = {"otpcode": code.upper(), "number": clean_number}
    try:
        verify_resp = requests.post(
            f"{DAWUROBO_BASE}/verify",
            json=verify_payload,
            headers=HEADERS,
            timeout=10
        )
        data = verify_resp.json()

        success = (
            data.get("status") == "success"
            or data.get("success") is True
        )

        if success:
            requests.post(
                f"{DAWUROBO_BASE}/invalidate",
                json={"number": clean_number},
                headers=HEADERS,
                timeout=3
            )
            logger.info(f"OTP verified and invalidated for {phone_number}")
            return True
        else:
            logger.warning(f"Invalid OTP attempt: {verify_resp.text}")
            return False
    except Exception as e:
        logger.error(f"OTP verify/invalidate failed: {e}")
        return False


@shared_task
def send_group_join_request_email_async(request_id: int):
    try:
        join_request = GroupJoinRequest.objects.select_related(
            'group__admin', 'user__profile'
        ).get(pk=request_id)
    except GroupJoinRequest.DoesNotExist:
        print(f"ERROR: GroupJoinRequest with ID {request_id} not found.")
        return False

    group = join_request.group
    admin_user = group.admin
    requester_name = join_request.user.profile.full_name

    # FIX: Build the frontend URL (where the admin actually navigates), not
    # a backend API URL. The admin uses the frontend app to review requests.
    frontend_url = getattr(settings, 'FRONTEND_URL', settings.SITE_URL)
    # Link to the frontend Requests page with the group context
    full_review_url = f"{frontend_url}/dashboard?page=Requests&group={group.id}"

    context = {
        'admin_name': admin_user.profile.full_name,
        'group_name': group.group_name,
        'requester_name': requester_name,
        'review_url': full_review_url,
    }

    email_html_content = render_to_string('emails/new_join_request.html', context)
    email_text_content = (
        f"A new user, {requester_name}, has requested to join your group: {group.group_name}. "
        f"Review the request here: {full_review_url}"
    )

    try:
        send_mail(
            subject=f"🚀 New Join Request for '{group.group_name}'",
            message=email_text_content,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[admin_user.email],
            html_message=email_html_content,
            fail_silently=False,
        )
        return True
    except Exception as e:
        print(f"EMAIL SEND ERROR for Group ID {group.id}: {e}")
        return False


@shared_task
def send_group_join_response_email_async(request_id: int, action: str):
    try:
        join_request = GroupJoinRequest.objects.select_related(
            'group__admin', 'user__profile'
        ).get(pk=request_id)
    except GroupJoinRequest.DoesNotExist:
        print(f"ERROR: GroupJoinRequest with ID {request_id} not found for response.")
        return False

    group = join_request.group
    applicant_user = join_request.user
    applicant_name = applicant_user.profile.full_name
    clean_group_name = group.group_name.strip('*').strip()

    frontend_url = getattr(settings, 'FRONTEND_URL', settings.SITE_URL)

    if group.public_id:
        full_group_url = f"{frontend_url}/dashboard?page=Groups&group={group.public_id}"
    else:
        full_group_url = f"{frontend_url}/dashboard?page=Groups"

    if action == 'approved':
        subject = f"🎉 Welcome! You've Joined '{clean_group_name}'"
        template_name = 'emails/join_request_approved.html'
    elif action == 'rejected':
        subject = f"😔 Update: Request to Join '{clean_group_name}'"
        template_name = 'emails/join_request_rejected.html'
    else:
        print(f"ERROR: Invalid action '{action}' passed to email task.")
        return False

    context = {
        'applicant_name': applicant_name,
        'group_name': group.group_name,
        'group_url': full_group_url,
        'action': action,
        'admin_name': group.admin.profile.full_name,
    }

    email_html_content = render_to_string(template_name, context)
    email_text_content = (
        f"Update for group '{clean_group_name}': Your request was {action}. "
        f"Log in to view details: {full_group_url}"
    )

    try:
        send_mail(
            subject=subject,
            message=email_text_content,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[applicant_user.email],
            html_message=email_html_content,
            fail_silently=False,
        )
        print(f"Group join response '{action}' email sent to {applicant_user.email}")
        return True
    except Exception as e:
        print(f"EMAIL SEND ERROR for Request ID {request_id}: {e}")
        return False


@shared_task
def process_daily_payouts():
    """
    Daily orchestrator for group payouts.
    Now fully safe against QStash queuing failures.
    We only mark last_payout_processed_date AFTER successfully queuing the task.
    """
    today = timezone.now().date()

    queryset = SavingsGroup.objects.filter(
        status='active',
        start_date__lte=today,
    ).filter(
        models.Q(last_payout_processed_date__isnull=True) |
        models.Q(last_payout_processed_date__lt=today)
    ).only(
        'id', 'start_date', 'payout_interval_days', 'last_payout_processed_date'
    ).iterator(chunk_size=1000)

    dispatched_count = 0

    for group in queryset:
        days_since_start = (today - group.start_date).days
        if days_since_start <= 0 or days_since_start % group.payout_interval_days != 0:
            continue

        # ── Pessimistic lock + re-check (protects against concurrent runs) ──
        with transaction.atomic():
            locked_group = SavingsGroup.objects.select_for_update().get(id=group.id)

            if locked_group.last_payout_processed_date == today:
                logger.info(f"Group {group.id} already processed today by another orchestrator. Skipping.")
                continue

            # IMPORTANT: Do NOT save last_payout_processed_date yet

        # ── Queue FIRST, mark only on success ─────────────────────────────
        try:
            execute_group_payout.delay(group.id)
            dispatched_count += 1

            # Only now mark the group as processed (queuing succeeded)
            SavingsGroup.objects.filter(id=group.id).update(
                last_payout_processed_date=today
            )

            logger.info({
                "event": "daily_payout_queued",
                "group_id": group.id,
                "today": str(today),
            })

        except Exception as e:  # Catches QStash HTTP errors, connection issues, etc.
            logger.error(
                f"Failed to queue payout for group {group.id}. "
                f"Will retry on next daily run. Error: {e}"
            )
            # Do NOT mark as processed → it will be picked up again tomorrow
            continue

    logger.info(f"Daily payout orchestrator completed. Dispatched {dispatched_count} tasks.")
    return f"Orchestrator finished. Dispatched {dispatched_count} payout tasks."

@shared_task
def execute_group_payout(group_id: int):
    """
    Two-phase payout with safer retry semantics and full idempotency.

    Phase 1  — Validate + reserve the payout atomically (ledger entries written,
               PaystackTransaction created with status="pending").
    Phase 2  — Initiate Paystack transfer outside the transaction.
               On PaystackError the tx_record is explicitly marked "failed"
               so retry_failed_payouts can act on it immediately. Previously,
               a failed Phase 2 would leave the record stuck at "pending" with
               no recovery path because:
                 - retry_failed_payouts only queries status="failed"
                 - sync_pending_transfers calls verify_transfer on a reference
                   that never reached Paystack, which itself raises PaystackError
                   (silently caught), leaving the record permanently stuck.
    """

    from .models import PaystackTransaction
    from .paystack import (
        PaystackError,
        create_transfer_recipient,
        generate_payout_reference,
        initiate_transfer,
    )
    from django.db import IntegrityError

    try:
        # ── Step 1: Determine which cycle we're processing ──────────────────
        group = SavingsGroup.objects.only(
            'id', 'current_cycle_number', 'expected_members',
            'total_pot_per_cycle', 'payout_interval_days', 'status'
        ).get(id=group_id)

        if group.status != 'active':
            logger.warning(f"Group {group_id} is not active. Status: {group.status}")
            return

        current_cycle = group.current_cycle_number
        payout_ref_prefix = f"snx-payout-{group.id}-c{current_cycle}-"
        ledger_ref = f"PAYOUT-GRP{group.id}-C{current_cycle}"

        # ── Idempotency checks ─────────────────────────────────────────────
        if PaystackTransaction.objects.filter(
            reference__startswith=payout_ref_prefix,
            status__in=["success", "pending"]
        ).exists():
            logger.info(f"Cycle {current_cycle} for Group {group_id} already has pending/success payout. Skipping.")
            return

        if LedgerEntry.objects.filter(reference=ledger_ref).exists():
            logger.info(f"Ledger entry already exists for Group {group_id} Cycle {current_cycle}. Skipping.")
            return

        # ── Validate contributions are complete ────────────────────────────
        verified_count = Contribution.objects.filter(
            membership__group=group,
            cycle_number=current_cycle,
            is_verified=True,
        ).count()

        if verified_count < group.expected_members:
            logger.error(
                f"Payout aborted: Group {group_id} Cycle {current_cycle} — "
                f"Only {verified_count}/{group.expected_members} contributions verified."
            )
            return

        # ── Determine beneficiary ──────────────────────────────────────────
        position = ((current_cycle - 1) % group.expected_members) + 1

        try:
            payout_order = PayoutOrder.objects.select_related(
                "membership__user__profile"
            ).get(group=group, position=position)
        except PayoutOrder.DoesNotExist:
            logger.critical({
                "event": "PAYOUT_ORDER_NOT_FOUND",
                "group_id": group_id,
                "cycle": current_cycle,
                "position": position,
                "expected_members": group.expected_members,
                "message": "Payout order missing — member may have been removed. Human intervention required."
            })
            # Optional: send alert to admins here if you have a notification service
            return  # Do NOT proceed — this payout cannot be completed automatically

        beneficiary = payout_order.membership.user
        beneficiary_profile = beneficiary.profile
        total_pot = group.total_pot_per_cycle

        if total_pot < Decimal("1.00"):
            logger.error({
                "event": "payout_below_minimum",
                "group_id": group_id,
                "cycle": current_cycle,
                "total_pot": str(total_pot),
            })
            return

        # ── Generate Paystack reference ─────────────────────────────────────
        payout_reference = generate_payout_reference(group.id, current_cycle)

        # ── PHASE 1: Atomic internal state changes ──────────────────────────
        with transaction.atomic():
            group = SavingsGroup.objects.select_for_update().get(id=group_id)

            # Double-check idempotency inside lock
            if PaystackTransaction.objects.filter(
                reference__startswith=payout_ref_prefix,
                status__in=["success", "pending"]
            ).exists() or LedgerEntry.objects.filter(reference=ledger_ref).exists():
                logger.info(f"Race condition detected — payout for cycle {current_cycle} already processed.")
                return

            try:
                LedgerService.transfer(
                    from_group=group,
                    to_user=beneficiary,
                    amount=total_pot,
                    transaction_type="payout",
                    description=f"Payout Cycle {current_cycle} to {beneficiary_profile.full_name}",
                    reference=ledger_ref,
                    actor=group.admin,
                    request_id=None,
                )
            except IntegrityError as e:
                if "ledgerentry_reference" in str(e).lower() or "unique" in str(e).lower():
                    logger.warning(f"Duplicate ledger entry detected for {ledger_ref} — payout was already processed.")
                    return
                raise

            tx_record = PaystackTransaction.objects.create(
                user=beneficiary,
                reference=payout_reference,
                transaction_type="payout",
                amount=total_pot,
                status="pending",
                related_group=group,
            )

        # ── PHASE 2: External Paystack call (outside transaction) ───────────
        try:
            # Get or create recipient
            if not beneficiary_profile.paystack_recipient_code:
                recipient_code = create_transfer_recipient(
                    name=beneficiary_profile.momo_name,
                    account_number=str(beneficiary_profile.momo_number),
                    momo_provider=beneficiary_profile.momo_provider,
                )
                beneficiary_profile.paystack_recipient_code = recipient_code
                beneficiary_profile.save(update_fields=["paystack_recipient_code"])
            else:
                recipient_code = beneficiary_profile.paystack_recipient_code

            transfer_data = initiate_transfer(
                recipient_code=recipient_code,
                amount_ghs=total_pot,
                reference=payout_reference,
                reason=f"SnappX Payout — {group.group_name} Cycle {current_cycle}",
            )

            # Success: advance cycle
            with transaction.atomic():
                group = SavingsGroup.objects.select_for_update().get(id=group_id)
                group.current_cycle_number += 1
                if current_cycle >= group.expected_members:
                    group.status = "completed"
                group.save(update_fields=["current_cycle_number", "status"])

            tx_record.paystack_reference = transfer_data.get("transfer_code", "")
            tx_record.save(update_fields=["paystack_reference"])

            # Notifications
            send_payout_notification_email_async.delay(
                beneficiary.id, group.id, current_cycle, str(total_pot)
            )
            NotificationService.notify_payout_received(
                beneficiary, group, total_pot, current_cycle
            )

            logger.info({
                "event": "group_payout_executed",
                "group_id": group.id,
                "cycle": current_cycle,
                "beneficiary_id": beneficiary.id,
                "amount": str(total_pot),
                "reference": payout_reference,
            })

        except PaystackError as exc:
            logger.error({
                "event": "payout_transfer_initiation_failed",
                "group_id": group_id,
                "cycle": current_cycle,
                "reference": payout_reference,
                "error": str(exc),
            })
            tx_record.status = "failed"
            tx_record.failure_reason = str(exc)
            tx_record.save(update_fields=["status", "failure_reason", "updated_at"])

    except SavingsGroup.DoesNotExist:
        logger.error(f"execute_group_payout: Group {group_id} not found.")
    except Exception as exc:
        logger.critical({
            "event": "PAYOUT_UNEXPECTED_ERROR",
            "group_id": group_id,
            "error": str(exc),
        })
        raise


@shared_task
def retry_failed_payouts():
    """
    Cron task to retry PaystackTransactions that are stuck in 'failed' status.

    There are two distinct failure modes this task handles:

    Mode A — "Never reached Paystack":
        execute_group_payout Phase 1 completed (ledger entries written) but
        Phase 2 raised PaystackError before or during initiate_transfer.
        The tx_record has status="failed" and paystack_reference is blank.
        Recovery: call initiate_transfer with a fresh reference directly.
        The ledger entries already exist — do NOT create new ones.

    Mode B — "Reached Paystack but status confirmed failed":
        A previous transfer attempt reached Paystack and came back as failed
        via webhook or sync_pending_transfers. The tx_record has status="failed"
        and paystack_reference is populated.
        Recovery: create a new transfer to the same recipient.
        Again, the ledger entries already exist.

    In both modes we only call initiate_transfer — we never create new ledger
    entries, since the group pot was already debited and the beneficiary was
    already credited in the original execute_group_payout Phase 1. Creating new
    ledger entries here would double-credit the beneficiary.
    """
    from .models import PaystackTransaction
    from .paystack import PaystackError, initiate_transfer, generate_payout_reference

    failed_payouts = PaystackTransaction.objects.filter(
        transaction_type="payout",
        status="failed",
    ).select_related("user__profile", "related_group")

    retried = 0

    for ps_tx in failed_payouts:
        profile = ps_tx.user.profile
        group = ps_tx.related_group

        if not group:
            logger.warning(
                f"retry_failed_payouts: No related_group on PaystackTransaction {ps_tx.id}. Skipping."
            )
            continue

        # Ensure we have a recipient code — create it if missing
        if not profile.paystack_recipient_code:
            try:
                from .paystack import create_transfer_recipient
                recipient_code = create_transfer_recipient(
                    name=profile.momo_name,
                    account_number=str(profile.momo_number),
                    momo_provider=profile.momo_provider,
                )
                profile.paystack_recipient_code = recipient_code
                profile.save(update_fields=["paystack_recipient_code"])
            except PaystackError as exc:
                logger.error({
                    "event": "retry_recipient_creation_failed",
                    "user_id": ps_tx.user_id,
                    "reference": ps_tx.reference,
                    "error": str(exc),
                })
                continue

        # Generate a fresh, Paystack-safe reference for this retry attempt.
        # We extract the original cycle from the failed reference when possible
        # for traceability, otherwise fall back to 0.
        try:
            # Original format: snx-payout-{group_id}-c{cycle}-a{attempt}-{uuid}
            cycle_part = ps_tx.reference.split("-c")[1].split("-")[0]
            original_cycle = int(cycle_part)
        except (IndexError, ValueError):
            original_cycle = 0

        new_reference = generate_payout_reference(group.id, original_cycle, attempt=2)

        try:
            transfer_data = initiate_transfer(
                recipient_code=profile.paystack_recipient_code,
                amount_ghs=ps_tx.amount,
                reference=new_reference,
                reason=f"SnappX Payout Retry — {group.group_name}",
            )

            # Create a new PaystackTransaction to track this retry attempt independently.
            # We do NOT create new LedgerEntries — the money movement already happened
            # in the original execute_group_payout Phase 1.
            PaystackTransaction.objects.create(
                user=ps_tx.user,
                reference=new_reference,
                paystack_reference=transfer_data.get("transfer_code", ""),
                transaction_type="payout",
                amount=ps_tx.amount,
                status="pending",
                related_group=group,
            )

            # Mark the original as superseded so it won't be retried again
            ps_tx.failure_reason = f"Superseded by retry: {new_reference}"
            ps_tx.save(update_fields=["failure_reason", "updated_at"])

            retried += 1
            logger.info({
                "event": "payout_retry_initiated",
                "original_reference": ps_tx.reference,
                "new_reference": new_reference,
                "user_id": ps_tx.user_id,
                "group_id": group.id,
                "amount": str(ps_tx.amount),
            })

        except PaystackError as exc:
            logger.error({
                "event": "payout_retry_failed",
                "reference": ps_tx.reference,
                "new_reference": new_reference,
                "group_id": group.id,
                "user_id": ps_tx.user_id,
                "error": str(exc),
            })

    return f"retry_failed_payouts: {retried} payout(s) retried."


@shared_task
def send_payout_notification_email_async(
    beneficiary_id: int,
    group_id: int,
    cycle: int,
    amount: str
) -> bool:
    try:
        from .models import SavingsGroup, User
        beneficiary = User.objects.select_related('profile').get(pk=beneficiary_id)
        group = SavingsGroup.objects.get(pk=group_id)
    except Exception:
        print(f"ERROR: User ID {beneficiary_id} or Group ID {group_id} not found.")
        return False

    beneficiary_name = beneficiary.profile.full_name
    group_name = group.group_name.strip('*').strip()
    momo_number = str(beneficiary.profile.momo_number)
    formatted_amount = f"₵{Decimal(amount):,.2f}"

    frontend_url = getattr(settings, 'FRONTEND_URL', settings.SITE_URL)
    full_dashboard_url = f"{frontend_url}/dashboard"

    context = {
        'beneficiary_name': beneficiary_name,
        'group_name': group_name,
        'cycle': cycle,
        'amount': formatted_amount,
        'momo_number': momo_number,
        'dashboard_url': full_dashboard_url,
    }

    subject = f"🎉 Payout Processed from '{group_name}'!"
    email_html_content = render_to_string('emails/payout_notification.html', context)
    email_text_content = (
        f"Congratulations, {beneficiary_name}! Your payout of {formatted_amount} "
        f"for cycle {cycle} in '{group_name}' has been processed. "
        f"It will be sent to your MoMo account: {momo_number}. "
        f"View your dashboard: {full_dashboard_url}"
    )

    try:
        send_mail(
            subject=subject,
            message=email_text_content,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[beneficiary.email],
            html_message=email_html_content,
            fail_silently=False,
        )
        return True
    except Exception as e:
        print(f"EMAIL SEND ERROR for Beneficiary ID {beneficiary_id}: {e}")
        return False


@shared_task
def send_goal_reminders():
    """
    FIXED: Properly handles NULL aggregation for goals with no contributions.
    Uses Coalesce to default NULL Sum results to 0 before comparison.
    """
    now = timezone.now()

    goals = SavingsGoal.objects.annotate(
        total_saved=Coalesce(
            Sum('contributions__amount', filter=models.Q(contributions__is_verified=True)),
            Value(Decimal('0.00'), output_field=DecimalField())
        )
    ).filter(
        total_saved__lt=F('target_amount')
    ).select_related('user__profile')

    for goal in goals:
        current_saved = goal.total_saved or Decimal('0.00')
        target = goal.target_amount

        if not goal.is_due:
            continue

        if goal.last_reminded_at and (now - goal.last_reminded_at) < timedelta(days=1):
            continue

        if target > 0:
            progress_percentage = float(round((current_saved / target) * 100, 1))
        else:
            progress_percentage = 0.0

        user = goal.user
        user_name = user.profile.full_name or "there"

        frontend_url = getattr(settings, 'FRONTEND_URL', settings.SITE_URL)
        dashboard_url = f"{frontend_url}/dashboard?page=Goals"

        context = {
            'user_name': user_name,
            'goal_name': goal.name,
            'contribution_amount': f"₵{goal.regular_contribution:,.2f}",
            'frequency': goal.get_frequency_display(),
            'current_saved': f"₵{current_saved:,.2f}",
            'target_amount': f"₵{target:,.2f}",
            'progress_percentage': progress_percentage,
            'target_date': goal.target_date.strftime('%B %d, %Y'),
            'dashboard_url': dashboard_url,
        }

        subject = f"⏰ Reminder: Your {goal.get_frequency_display()} contribution for '{goal.name}'"
        html_content = render_to_string('emails/goal_reminder.html', context)
        text_content = (
            f"Hi {user_name},\n\nFriendly reminder to make your {goal.get_frequency_display()} "
            f"contribution of ₵{goal.regular_contribution:,.2f} towards your goal: \"{goal.name}\".\n\n"
            f"Progress: {progress_percentage}% (₵{current_saved:,.2f} of ₵{target:,.2f})\n"
            f"Target Date: {goal.target_date.strftime('%B %d, %Y')}\n\n"
            f"Log in here to contribute: {dashboard_url}"
        )

        try:
            send_mail(
                subject=subject,
                message=text_content,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                html_message=html_content,
                fail_silently=False,
            )
            goal.last_reminded_at = now
            goal.save(update_fields=['last_reminded_at'])
            NotificationService.notify_goal_reminder(goal)
            logger.info(f"Goal reminder sent to {user.email} for goal: {goal.name}")
        except Exception as e:
            logger.error(f"Failed to send goal reminder to {user.id}: {str(e)}")


def send_reconciliation_alert_to_admins(mismatches):
    """
    FIXED: settings.ADMINS is a list of (name, email) tuples.
    The original code treated it as a flat list of email strings.
    """
    admin_emails = [email for name, email in settings.ADMINS] if settings.ADMINS else []

    if not admin_emails:
        admin_emails = [settings.DEFAULT_FROM_EMAIL]

    subject = f"🚨 URGENT: Financial Reconciliation Mismatch Detected - {timezone.now().date()}"

    mismatch_report = ""
    for item in mismatches:
        mismatch_report += (
            f"User: {item['user']}\n"
            f"  - Ledger Total: {item['ledger']}\n"
            f"  - Wallet Cache: {item['cache']}\n"
            f"  - Difference:   {item['diff']}\n"
            f"-------------------------------------\n"
        )

    message = (
        f"The daily reconciliation task found {len(mismatches)} account(s) with balance drift.\n\n"
        f"Mismatched Accounts:\n{mismatch_report}\n"
        f"Action Required: Check the LedgerEntry logs for these users to identify the bug."
    )

    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=admin_emails,
        fail_silently=False,
    )


@shared_task
def reconcile_financial_integrity():
    """
    The Case/When expression for calculating balance now correctly uses
    ExpressionWrapper with proper output_field to avoid type resolution errors.
    """
    mismatches = []
    wallets = Wallet.objects.select_related('user').all().iterator()

    for wallet in wallets:
        stats = LedgerEntry.objects.filter(wallet=wallet).aggregate(
            balance=Sum(
                Case(
                    When(direction='credit', then=F('amount')),
                    When(direction='debit', then=F('amount') * Value(-1)),
                    default=Value(0),
                    output_field=DecimalField(max_digits=12, decimal_places=2)
                )
            )
        )

        calculated_truth = stats['balance'] or Decimal('0.00')

        if calculated_truth != wallet.current_balance:
            mismatches.append({
                "user": wallet.user.email if wallet.user else f"Group:{wallet.group_id}",
                "ledger": str(calculated_truth),
                "cache": str(wallet.current_balance),
                "diff": str(calculated_truth - wallet.current_balance)
            })
            logger.critical(
                f"BALANCE_DRIFT: {'User' if wallet.user else 'Group'} "
                f"{wallet.user.email if wallet.user else wallet.group_id} | "
                f"Expected: {calculated_truth} | Found: {wallet.current_balance}"
            )

    if mismatches:
        send_reconciliation_alert_to_admins(mismatches)

    return f"Reconciliation complete. {len(mismatches)} mismatch(es) found."


@shared_task
def clear_old_idempotency_keys():
    expiry_limit = timezone.now() - timedelta(days=7)
    count, _ = IdempotencyKey.objects.filter(created_at__lt=expiry_limit).delete()
    return f"Purged {count} idempotency keys."

@shared_task
def sync_pending_transfers():
    """
    Periodic safety net: checks Paystack for transfers still in 'pending'
    status after 30+ minutes and updates our records if Paystack shows
    a conclusive status (success / failed / reversed).

    Runs every hour. Prevents stuck records if webhooks fail to deliver.
    """
    from datetime import timedelta
    from .models import PaystackTransaction
    from .paystack import PaystackError, verify_transfer, verify_transaction

    cutoff = timezone.now() - timedelta(minutes=30)
    synced = 0

    # ── 1. Handle stale pending PAYOUTS and CASHOUTS ─────────────────────
    stale_pending = PaystackTransaction.objects.filter(
        transaction_type__in=["payout", "cashout"],
        status="pending",
        created_at__lt=cutoff,
    ).select_related("user")

    for ps_tx in stale_pending:
        try:
            data = verify_transfer(ps_tx.reference)
            paystack_status = data.get("status", "")

            if paystack_status == "success" and ps_tx.status != "success":
                ps_tx.status = "success"
                ps_tx.paystack_reference = data.get("transfer_code", ps_tx.paystack_reference)
                ps_tx.save(update_fields=["status", "paystack_reference", "updated_at"])
                synced += 1
                logger.info({
                    "event": "sync_pending_transfer_resolved_success",
                    "reference": ps_tx.reference,
                })

            elif paystack_status in ("failed", "reversed") and ps_tx.status == "pending":
                ps_tx.status = paystack_status
                ps_tx.save(update_fields=["status", "updated_at"])
                synced += 1
                logger.warning({
                    "event": f"sync_pending_transfer_resolved_{paystack_status}",
                    "reference": ps_tx.reference,
                })

        except PaystackError as exc:
            logger.error({
                "event": "sync_pending_transfer_verify_failed",
                "reference": ps_tx.reference,
                "error": str(exc),
            })

    # ── 2. Handle stale pending TOP-UPS (moved outside the loop) ─────────
    stale_topups = PaystackTransaction.objects.filter(
        transaction_type="topup",
        status="pending",
        created_at__lt=cutoff,
    ).select_related("user")

    for ps_tx in stale_topups:
        try:
            verified = verify_transaction(ps_tx.reference)
            # verify_transaction raises PaystackError if not "success"
            amount_ghs = Decimal(verified.get("amount", 0)) / 100

            with transaction.atomic():
                ps_tx_locked = PaystackTransaction.objects.select_for_update().get(pk=ps_tx.pk)
                if ps_tx_locked.status != "pending":
                    continue

                LedgerService.create_entry(
                    user=ps_tx.user,
                    actor=ps_tx.user,
                    amount=amount_ghs,
                    direction="credit",
                    transaction_type="deposit",
                    description="Wallet top-up (reconciled)",
                    reference=f"TOPUP-{ps_tx.reference}",
                    request_id=None,
                )
                ps_tx_locked.status = "success"
                ps_tx_locked.save(update_fields=["status", "updated_at"])
                synced += 1

        except PaystackError:
            pass

    return f"sync_pending_transfers: {synced} stale pending transaction(s) synced."
