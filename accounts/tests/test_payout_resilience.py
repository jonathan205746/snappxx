import pytest
from decimal import Decimal
from accounts.tasks import execute_group_payout
from accounts.models import Contribution, LedgerEntry

@pytest.mark.django_db
class TestPayoutResilience:

    def test_payout_aborts_on_insufficient_verified_funds(self, test_group, membership):
        """
        SCENARIO: A group expects 5 members to pay, but only 1 has a 'verified' contribution.
        EXPECTED: Task should log an error and return without transferring money.
        """
        Contribution.objects.create(
            membership=membership,
            amount=test_group.contribution_amount,
            cycle_number=1,
            is_verified=True
        )

        execute_group_payout(test_group.id)

        payout_ref = f"PAYOUT-GRP{test_group.id}-C1"
        assert not LedgerEntry.objects.filter(reference__contains=payout_ref).exists()

        test_group.refresh_from_db()
        assert test_group.current_cycle_number == 1

    def test_payout_double_spend_protection(self, test_group, test_user, membership):
        """
        SCENARIO: A payout task is triggered twice for the same cycle.
        EXPECTED: The second task should detect the existing LedgerEntry and abort.
        """
        payout_ref = f"PAYOUT-GRP{test_group.id}-C1"

        LedgerEntry.objects.create(
            wallet=test_user.wallet,
            amount=Decimal("500.00"),
            direction='credit',
            transaction_type='payout',
            reference=f"{payout_ref}-CR",
            actor=test_group.admin
        )

        execute_group_payout(test_group.id)

        assert LedgerEntry.objects.filter(reference__contains=payout_ref).count() == 1
