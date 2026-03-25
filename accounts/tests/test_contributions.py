import pytest
from decimal import Decimal
from django.urls import reverse
from rest_framework import status
from accounts.models import LedgerEntry, Contribution

@pytest.mark.django_db
def test_group_contribution_flow(auth_client, test_user, test_group, funded_wallet, membership):
    """
    Test the full contribution lifecycle:
    1. Successful 201 response.
    2. Wallet deduction.
    3. Creation of a Contribution record.
    4. Creation of a 'debit' LedgerEntry.
    5. Idempotency safety.
    """
    url = reverse('group-contribute', kwargs={'group_id': test_group.id})
    data = {"amount": 100.00}
    headers = {'HTTP_X_IDEMPOTENCY_KEY': 'contribution-123'}

    # Execute contribution
    response = auth_client.post(url, data, **headers)
    assert response.status_code == status.HTTP_201_CREATED

    # Verify Wallet Balance (500.00 - 100.00)
    test_user.wallet.refresh_from_db()
    assert test_user.wallet.current_balance == Decimal("400.00")

    # Verify Contribution record was created
    # This ensures the user is tracked for the current cycle
    assert Contribution.objects.filter(
        membership=membership,
        cycle_number=test_group.current_cycle_number
    ).exists()

    # Verify LedgerEntry (The Audit Trail)
    ledger = LedgerEntry.objects.filter(
        wallet=test_user.wallet,
        transaction_type='contribution',
        direction='debit'
    ).first()

    assert ledger is not None
    assert ledger.amount == Decimal("100.00")

    # TEST IDEMPOTENCY: Re-send same request
    # The view should return the cached 201 response without deducting again.
    response2 = auth_client.post(url, data, **headers)
    assert response2.status_code == status.HTTP_201_CREATED

    # Wallet should STILL be 400, not 300
    test_user.wallet.refresh_from_db()
    assert test_user.wallet.current_balance == Decimal("400.00")

    # Ensure no duplicate ledger entries were created
    assert LedgerEntry.objects.filter(wallet=test_user.wallet).count() == 1
