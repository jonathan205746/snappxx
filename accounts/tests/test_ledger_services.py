import pytest
from decimal import Decimal
from accounts.services import LedgerService
from django.core.exceptions import ValidationError

@pytest.mark.django_db
def test_transfer_fails_if_source_insufficient(test_user, test_group, funded_wallet):
    """
    Service Layer Integrity: LedgerService.transfer must raise ValidationError
    if the source wallet dips below zero, ensuring we never payout ghost money.
    """
    # Ensure Group Wallet is empty
    group_wallet = test_group.group_wallet
    group_wallet.current_balance = Decimal("0.00")
    group_wallet.save()

    # Attempt Transfer (Group -> User)
    with pytest.raises(ValidationError) as excinfo:
        LedgerService.transfer(
            amount=Decimal("500.00"),
            transaction_type='payout',
            description="Illegal Payout",
            reference="FAIL-TEST-001",
            from_group=test_group,
            to_user=test_user
        )

    assert "Insufficient funds" in str(excinfo.value)

    # Verify Balances remain untouched
    group_wallet.refresh_from_db()
    test_user.wallet.refresh_from_db()
    assert group_wallet.current_balance == 0
    assert test_user.wallet.current_balance == 500
