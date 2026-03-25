import pytest
from accounts.models import Contribution, LedgerEntry, PayoutOrder
from accounts.tasks import execute_group_payout
from decimal import Decimal

@pytest.mark.django_db
class TestGroupTimeLogic:

    def test_cycle_transition_logic(self, test_group, membership):
        test_group.current_cycle_number = 1
        test_group.expected_members = 1
        test_group.save()

        # Seed the Group Wallet Ledger so it has money to pay out
        LedgerEntry.objects.create(
            wallet=test_group.group_wallet,
            amount=Decimal("100.00"),
            direction='credit',
            transaction_type='other',
            reference="SEED-FUNDS-001"
        )
        # Update cached balance to match ledger
        test_group.group_wallet.current_balance = Decimal("100.00")
        test_group.group_wallet.save()

        # Mock the Contribution record
        Contribution.objects.create(
            membership=membership,
            amount=Decimal("100.00"),
            cycle_number=1,
            is_verified=True
        )

        # Mock the Payout Order
        PayoutOrder.objects.get_or_create(group=test_group, membership=membership, position=1)

        # Run task
        execute_group_payout(test_group.id)

        test_group.refresh_from_db()
        assert test_group.current_cycle_number == 2

    def test_group_completion_logic(self, test_group, membership):
        """Group should hit 'completed' when current_cycle >= expected_members."""
        test_group.expected_members = 1
        test_group.current_cycle_number = 1
        test_group.status = 'active'
        test_group.save()

        amount = test_group.contribution_amount

        # Update the actual Wallet balance
        test_group.group_wallet.current_balance = amount
        test_group.group_wallet.save()

        # Create the Ledger Entry
        LedgerEntry.objects.create(
            wallet=test_group.group_wallet,
            amount=amount,
            direction='credit',
            transaction_type='contribution',
            reference="SEED-COMPLETION-TEST"
        )

        # Create the contribution record
        Contribution.objects.create(
            membership=membership,
            amount=amount,
            cycle_number=1,
            is_verified=True
        )

        PayoutOrder.objects.get_or_create(group=test_group, membership=membership, position=1)

        execute_group_payout(test_group.id)

        test_group.refresh_from_db()
        assert test_group.status == 'completed'
