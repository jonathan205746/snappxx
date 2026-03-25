import pytest
from decimal import Decimal
from accounts.tasks import execute_group_payout
from accounts.models import PayoutOrder, Contribution, LedgerEntry

@pytest.mark.django_db
def test_payout_fails_gracefully_missing_payout_order(test_group, membership, funded_wallet):
    """
    Logic Gap: If a PayoutOrder is deleted, the task shouldn't pay a random person
    or crash silently. It currently raises an Exception which Celery retries.
    We confirm it DOES fail (so we can monitor dead letter queues).
    """
    test_group.status = 'active'
    test_group.current_cycle_number = 1
    test_group.expected_members = 1
    test_group.save()

    test_group.group_wallet.current_balance = Decimal("100.00")
    test_group.group_wallet.save()

    Contribution.objects.create(
        membership=membership, amount=100, cycle_number=1, is_verified=True
    )

    PayoutOrder.objects.filter(group=test_group).delete()

    with pytest.raises(PayoutOrder.DoesNotExist):
        execute_group_payout(test_group.id)

    assert not LedgerEntry.objects.filter(transaction_type='payout').exists()

    test_group.refresh_from_db()
    assert test_group.current_cycle_number == 1
