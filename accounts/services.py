# accounts/services.py

import logging
import uuid
from django.db import transaction
from decimal import Decimal
from django.core.exceptions import ValidationError
from .models import Wallet, LedgerEntry

logger = logging.getLogger('accounts.finance')

class LedgerService:
    @staticmethod
    def create_entry(
        amount, direction, transaction_type, description, reference,
        user=None, group=None, actor=None, request_id=None, goal=None,
        transaction_group_id=None, related_group=None, related_goal=None
    ):
        """
        Atomic Single Source of Truth update.
        Handles both personal User wallets and Group Virtual Wallets (Pots).
        """
        with transaction.atomic():

            # Identify and initialize the Wallet
            if user:
                wallet, _ = Wallet.objects.get_or_create(user=user)
                owner_label = user.email
            elif group:
                wallet, _ = Wallet.objects.get_or_create(group=group)
                owner_label = f"GROUP_POT: {group.group_name}"
            else:
                raise ValidationError("A ledger entry must be associated with either a User or a Group.")

            # Lock the row
            wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)

            # Assign the Actor
            effective_actor = actor or user or (group.admin if group else None)
            if not effective_actor:
                raise ValidationError("An actor (User) must be identified for the audit trail.")

            # Create immutable Ledger Record
            entry = LedgerEntry.objects.create(
                wallet=wallet,
                actor=effective_actor,
                amount=amount,
                direction=direction,
                transaction_type=transaction_type,
                description=description,
                reference=reference,
                request_id=request_id,
                related_group=related_group if related_group is not None else group,
                related_goal=related_goal if related_goal is not None else goal,
                transaction_group_id=transaction_group_id,
            )
            # Update Wallet Balance
            if direction == 'credit':
                wallet.current_balance += amount
            else:
                wallet.current_balance -= amount
            wallet.save()

            logger.info({
                "event": "ledger_transaction_created",
                "transaction_id": str(entry.id),
                "wallet_owner": owner_label,
                "amount": str(amount),
                "direction": direction,
                "new_balance": str(wallet.current_balance),
                "transaction_group_id": str(transaction_group_id) if transaction_group_id else None
            })
            return entry

    @staticmethod
    def transfer(
        amount, transaction_type, description, reference, *,
        from_user=None, from_group=None,
        to_user=None, to_group=None,
        actor=None, request_id=None,
        related_group=None, related_goal=None
    ):
        """
        Convenience helper for internal transfers (Double-Entry).
        Ensures both the Debit and Credit legs happen inside a single database transaction.
        """
        source_count = sum(1 for x in [from_user, from_group] if x is not None)
        dest_count = sum(1 for x in [to_user, to_group] if x is not None)
        if source_count != 1 or dest_count != 1:
            raise ValidationError("Transfer must have exactly one source and one destination wallet.")

        related_group = related_group or from_group or to_group
        related_goal = related_goal

        with transaction.atomic():
            transaction_group_id = uuid.uuid4()

            # Perform debit (source )
            debit_entry = LedgerService.create_entry(
                amount=amount,
                direction='debit',
                transaction_type=transaction_type,
                description=f"{description} (Debit)",
                reference=f"{reference}-DB",
                user=from_user,
                group=from_group,
                actor=actor,
                request_id=request_id,
                goal=related_goal,
                related_group=related_group,
                transaction_group_id=transaction_group_id
            )

            # CONSERVATION OF MONEY CHECK
            # We verify the source wallet after the debit.
            # If the source (especially a Group Pot) goes negative, we abort.
            source_wallet = debit_entry.wallet
            if source_wallet.current_balance < 0:
                raise ValidationError(
                    f"Insufficient funds. Source balance would be {source_wallet.current_balance}"
                )

            # Perform Credit (Destination)
            LedgerService.create_entry(
                amount=amount,
                direction='credit',
                transaction_type=transaction_type,
                description=f"{description} (Credit)",
                reference=f"{reference}-CR",
                user=to_user,
                group=to_group,
                actor=actor,
                request_id=request_id,
                goal=related_goal,
                related_group=related_group,
                transaction_group_id=transaction_group_id
            )
            return transaction_group_id

    @staticmethod
    def get_balance(user=None, group=None, verify_integrity=False):
        """
        Retrieves the balance for either a User or a Group.
        High-performance read with an optional integrity check.
        """
        if not user and not group:
            return Decimal('0.00')
        try:
            if user:
                wallet = Wallet.objects.get(user=user)
            else:
                wallet = Wallet.objects.get(group=group)

            if verify_integrity:
                from django.db.models import Sum, Case, When, Value
                from .models import LedgerEntry
                agg = LedgerEntry.objects.filter(wallet=wallet).aggregate(
                    calc_balance=Sum(
                        Case(
                            When(direction='credit', then='amount'),
                            When(direction='debit', then=-Value(1) * 'amount'),
                            default=Value(0)
                        )
                    )
                )
                calculated = agg['calc_balance'] or Decimal('0.00')
                if calculated != wallet.current_balance:
                    logger.critical(f"BALANCE MISMATCH: Wallet {wallet.id}. Cached: {wallet.current_balance}, Ledger: {calculated}")
            return wallet.current_balance
        except Wallet.DoesNotExist:
            logger.error(f"Missing wallet accessed for {'user ' + str(user.id) if user else 'group ' + str(group.id)}")
            return Decimal('0.00')
