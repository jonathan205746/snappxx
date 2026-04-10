# accounts/services.py

import logging
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q

logger = logging.getLogger("accounts.finance")

class LedgerService:

    @staticmethod
    @transaction.atomic
    def create_entry(
        amount: Decimal,
        direction: str,
        transaction_type: str,
        description: str,
        reference: str,
        user=None,
        group=None,
        actor=None,
        request_id=None,
        related_group=None,
        related_goal=None,
        transaction_group_id=None,
        goal=None,
    ):
        from .models import Wallet, LedgerEntry

        if user:
            owner_label = user.email
        elif group:
            owner_label = f"GROUP_POT: {group.group_name}"
        else:
            raise ValidationError("Ledger entry must be linked to a user or group.")

        effective_actor = actor or user or (group.admin if group else None)
        if not effective_actor:
            raise ValidationError("An actor is required for audit trail.")

        if direction == "debit":
            # ── ATOMIC conditional debit ─────────────────────────────────────
            # UPDATE wallet SET current_balance = current_balance - %s
            # WHERE (user_id=%s OR group_id=%s) AND current_balance >= %s
            # Returns the number of rows updated (1 = success, 0 = insufficient funds).
            if user:
                updated = Wallet.objects.filter(
                    user=user,
                    current_balance__gte=amount,
                ).update(
                    current_balance=F("current_balance") - amount,
                )
            else:
                updated = Wallet.objects.filter(
                    group=group,
                    current_balance__gte=amount,
                ).update(
                    current_balance=F("current_balance") - amount,
                )

            if updated == 0:
                # Either wallet doesn't exist, or balance was insufficient.
                # Distinguish the two cases for better error messages.
                try:
                    if user:
                        wallet = Wallet.objects.get(user=user)
                    else:
                        wallet = Wallet.objects.get(group=group)
                    # Wallet exists but balance was too low
                    raise ValidationError(
                        f"Insufficient wallet balance. "
                        f"Available: ₵{wallet.current_balance:,.2f}, "
                        f"Required: ₵{amount:,.2f}"
                    )
                except Wallet.DoesNotExist:
                    raise ValidationError(
                        f"Wallet not found for {'user ' + str(user.id) if user else 'group ' + str(group.id)}"
                    )

            # Re-fetch the wallet to get the updated balance for the log
            if user:
                wallet = Wallet.objects.get(user=user)
            else:
                wallet = Wallet.objects.get(group=group)

        else:  # credit
            # Simple F() addition — always safe
            if user:
                updated = Wallet.objects.filter(user=user).update(
                    current_balance=F("current_balance") + amount,
                )
            else:
                updated = Wallet.objects.filter(group=group).update(
                    current_balance=F("current_balance") + amount,
                )

            if updated == 0:
                raise ValidationError(
                    f"Wallet not found for credit operation on "
                    f"{'user ' + str(user.id) if user else 'group ' + str(group.id)}"
                )

            if user:
                wallet = Wallet.objects.get(user=user)
            else:
                wallet = Wallet.objects.get(group=group)

        # ── Write the immutable ledger entry ─────────────────────────────────
        entry = LedgerEntry.objects.create(
            wallet=wallet,
            actor=effective_actor,
            amount=amount,
            direction=direction,
            transaction_type=transaction_type,
            description=description,
            reference=reference,
            request_id=request_id,
            related_group=related_group or group,
            related_goal=related_goal or goal,
            transaction_group_id=transaction_group_id,
        )

        logger.info({
            "event": "ledger_transaction_created",
            "transaction_id": str(entry.id),
            "wallet_owner": owner_label,
            "amount": str(amount),
            "direction": direction,
            "new_balance": str(wallet.current_balance),
            "transaction_group_id": str(transaction_group_id) if transaction_group_id else None,
        })

        return entry

    @staticmethod
    @transaction.atomic
    def transfer(
        amount: Decimal,
        transaction_type: str,
        description: str,
        reference: str,
        *,
        from_user=None,
        from_group=None,
        to_user=None,
        to_group=None,
        actor=None,
        request_id=None,
        related_group=None,
        related_goal=None,
    ):
        from django.core.exceptions import ValidationError as DjangoValidationError

        if sum(x is not None for x in [from_user, from_group]) != 1:
            raise DjangoValidationError("Exactly one source is required.")
        if sum(x is not None for x in [to_user, to_group]) != 1:
            raise DjangoValidationError("Exactly one destination is required.")

        _related_group = related_group or from_group or to_group
        transaction_group_id = uuid.uuid4()

        try:
            LedgerService.create_entry(
                amount=amount,
                direction="debit",
                transaction_type=transaction_type,
                description=f"{description} (Debit)",
                reference=f"{reference}-DB",
                user=from_user,
                group=from_group,
                actor=actor,
                request_id=request_id,
                related_group=_related_group,
                related_goal=related_goal,
                transaction_group_id=transaction_group_id,
            )

            LedgerService.create_entry(
                amount=amount,
                direction="credit",
                transaction_type=transaction_type,
                description=f"{description} (Credit)",
                reference=f"{reference}-CR",
                user=to_user,
                group=to_group,
                actor=actor,
                request_id=request_id,
                related_group=_related_group,
                related_goal=related_goal,
                transaction_group_id=transaction_group_id,
            )

            return transaction_group_id

        except DjangoValidationError as e:
            if "Insufficient wallet balance" in str(e):
                raise DjangoValidationError(
                    "Insufficient wallet balance for this transaction."
                )
            raise

    @staticmethod
    def get_balance(user=None, group=None, verify_integrity=False):
        from decimal import Decimal
        from django.db.models import Sum, Case, When, Value, F, DecimalField
        from .models import Wallet, LedgerEntry

        if not user and not group:
            return Decimal("0.00")

        try:
            if user:
                wallet = Wallet.objects.get(user=user)
            else:
                wallet = Wallet.objects.get(group=group)

            if verify_integrity:
                agg = LedgerEntry.objects.filter(wallet=wallet).aggregate(
                    calc_balance=Sum(
                        Case(
                            When(direction="credit", then=F("amount")),
                            When(direction="debit", then=F("amount") * Value(-1)),
                            default=Value(0),
                            output_field=DecimalField(max_digits=12, decimal_places=2),
                        )
                    )
                )
                calculated = agg["calc_balance"] or Decimal("0.00")
                if calculated != wallet.current_balance:
                    logger.critical(
                        f"BALANCE MISMATCH: Wallet {wallet.id}. "
                        f"Cached: {wallet.current_balance}, Ledger: {calculated}"
                    )

            return wallet.current_balance

        except Wallet.DoesNotExist:
            logger.error(
                f"Missing wallet for "
                f"{'user ' + str(user.id) if user else 'group ' + str(group.id)}"
            )
            return Decimal("0.00")


class NotificationService:
    """
    Factory for every in-app notification.

    Design principles:
    - All public methods are 'fire-and-forget' - they NEVER raise.
      A notification failure must NEVER break the calling transaction.
    - bulk_create is used for fan-out events (group activation, etc.)
    - action_url contains the frontend DashboardPage name for routing.
    """

    @staticmethod
    def _create(
        user,
        notification_type: str,
        title: str,
        message: str,
        related_group=None,
        related_goal=None,
        action_url: str = None,
        metadata: dict = None,
    ):
        """Low-level factory. Do NOT call outside this class."""
        from .models import Notification
        try:
            return Notification.objects.create(
                user=user,
                notification_type=notification_type,
                title=title,
                message=message,
                related_group=related_group,
                related_goal=related_goal,
                action_url=action_url,
                metadata=metadata or {},
            )
        except Exception as exc:  # pragma: no cover
            logger.error(f"[NotificationService] Failed to create '{notification_type}' for user {getattr(user, 'id', '?')}: {exc}")
            return None

    #  Group events
    @staticmethod
    def notify_join_request(join_request):
        """
        Fires when a user submits a join request.
        Target: the GROUP ADMIN.
        """
        try:
            requester_name = join_request.user.profile.full_name
            group           = join_request.group
            NotificationService._create(
                user=group.admin,
                notification_type='join_request',
                title='New Join Request',
                message=f'{requester_name} wants to join "{group.group_name}"',
                related_group=group,
                action_url='Requests',
                metadata={
                    'request_id':     join_request.id,
                    'requester_name': requester_name,
                    'requester_id':   join_request.user.id,
                    'group_name':     group.group_name,
                },
            )
        except Exception as exc:
            logger.error(f"[NotificationService.notify_join_request] {exc}")

    @staticmethod
    def notify_join_approved(join_request):
        """
        Fires when admin approves.
        Target: the APPLICANT.
        """
        try:
            group = join_request.group
            NotificationService._create(
                user=join_request.user,
                notification_type='join_approved',
                title='Join Request Approved! 🎉',
                message=f'You\'ve been approved to join "{group.group_name}". Welcome aboard!',
                related_group=group,
                action_url='Groups',
                metadata={
                    'group_id':   group.id,
                    'group_name': group.group_name,
                    'admin_name': group.admin.profile.full_name,
                },
            )
        except Exception as exc:
            logger.error(f"[NotificationService.notify_join_approved] {exc}")

    @staticmethod
    def notify_join_rejected(join_request):
        """
        Fires when admin rejects.
        Target: the APPLICANT.
        """
        try:
            group = join_request.group
            NotificationService._create(
                user=join_request.user,
                notification_type='join_rejected',
                title='Join Request Update',
                message=f'Your request to join "{group.group_name}" was not approved. You may re-apply.',
                related_group=group,
                action_url='Groups',
                metadata={
                    'group_id':   group.id,
                    'group_name': group.group_name,
                },
            )
        except Exception as exc:
            logger.error(f"[NotificationService.notify_join_rejected] {exc}")

    @staticmethod
    def notify_new_member(admin, new_user, group):
        """
        Fires alongside notify_join_approved.
        Target: the GROUP ADMIN (confirmation + headcount awareness).
        """
        try:
            NotificationService._create(
                user=admin,
                notification_type='new_member',
                title='New Member Joined',
                message=f'{new_user.profile.full_name} has joined "{group.group_name}" ({group.current_members}/{group.expected_members} members)',
                related_group=group,
                action_url='Groups',
                metadata={
                    'member_id':      new_user.id,
                    'member_name':    new_user.profile.full_name,
                    'group_id':       group.id,
                    'current_count':  group.current_members,
                    'expected_count': group.expected_members,
                },
            )
        except Exception as exc:
            logger.error(f"[NotificationService.notify_new_member] {exc}")

    @staticmethod
    def notify_group_activated(group):
        """
        Fires when the group reaches full capacity and payout cycles begin.
        Target: ALL MEMBERS — uses bulk_create for efficiency.
        """
        from .models import Notification
        try:
            memberships = group.memberships.select_related('user').all()
            notifications = [
                Notification(
                    user=m.user,
                    notification_type='group_activated',
                    title='Your Group Is Live! 🚀',
                    message=f'"{group.group_name}" is full. Payout cycles have officially started!',
                    related_group=group,
                    action_url='Groups',
                    metadata={
                        'group_id':   group.id,
                        'group_name': group.group_name,
                        'start_date': str(group.start_date),
                    },
                )
                for m in memberships
            ]
            if notifications:
                Notification.objects.bulk_create(notifications, ignore_conflicts=True)
                logger.info(f"[NotificationService] Bulk created {len(notifications)} group_activated notifications for group {group.id}")
        except Exception as exc:
            logger.error(f"[NotificationService.notify_group_activated] {exc}")

    #  Payout events
    @staticmethod
    def notify_payout_received(user, group, amount, cycle: int):
        """
        Fires after a successful group payout disbursement.
        Target: the BENEFICIARY.
        """
        try:
            from decimal import Decimal
            amt = Decimal(str(amount))
            NotificationService._create(
                user=user,
                notification_type='payout_received',
                title='Payout Processed! 💰',
                message=f'₵{amt:,.2f} from "{group.group_name}" (Cycle {cycle}) will be sent to your MoMo shortly.',
                related_group=group,
                action_url='Dashboard',
                metadata={
                    'amount':     str(amt),
                    'group_id':   group.id,
                    'group_name': group.group_name,
                    'cycle':      cycle,
                },
            )
        except Exception as exc:
            logger.error(f"[NotificationService.notify_payout_received] {exc}")

    #  Goal events
    @staticmethod
    def notify_goal_reminder(goal):
        """
        Fires on the goal's contribution due date.
        Target: the GOAL OWNER.
        """
        try:
            from decimal import Decimal
            amt = Decimal(str(goal.regular_contribution))
            NotificationService._create(
                user=goal.user,
                notification_type='goal_reminder',
                title='Goal Contribution Due ⏰',
                message=f'Time to add ₵{amt:,.2f} to "{goal.name}" — keep the momentum going!',
                related_goal=goal,
                action_url='Goals',
                metadata={
                    'goal_id':             goal.id,
                    'goal_name':           goal.name,
                    'contribution_amount': str(amt),
                    'frequency':           goal.frequency,
                },
            )
        except Exception as exc:
            logger.error(f"[NotificationService.notify_goal_reminder] {exc}")

    @staticmethod
    def notify_goal_completed(goal):
        """
        Fires when a goal reaches 100% completion.
        Target: the GOAL OWNER.
        """
        try:
            from decimal import Decimal
            target = Decimal(str(goal.target_amount))
            NotificationService._create(
                user=goal.user,
                notification_type='goal_completed',
                title='Goal Achieved! 🎯',
                message=f'Amazing! You\'ve hit your ₵{target:,.2f} target for "{goal.name}".',
                related_goal=goal,
                action_url='Goals',
                metadata={
                    'goal_id':       goal.id,
                    'goal_name':     goal.name,
                    'target_amount': str(target),
                },
            )
        except Exception as exc:
            logger.error(f"[NotificationService.notify_goal_completed] {exc}")
