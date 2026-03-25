import pytest
from decimal import Decimal
from django.core import mail
from accounts.models import Wallet
from accounts.services import LedgerService
from accounts.tasks import reconcile_financial_integrity

@pytest.mark.django_db
def test_watchdog_detects_balance_drift(test_user):
    """
    Simulate a database corruption event (Drift) and ensure
    the reconciliation task detects it and alerts admins.
    """
    LedgerService.create_entry(
        user=test_user,
        amount=Decimal("100.00"),
        direction='credit',
        transaction_type='deposit',
        description="Initial funding",
        reference="REC-TEST-001"
    )

    wallet = test_user.wallet
    wallet.refresh_from_db()
    assert wallet.current_balance == Decimal("100.00")

    Wallet.objects.filter(id=wallet.id).update(current_balance=Decimal("500.00"))

    reconcile_financial_integrity()

    assert len(mail.outbox) == 1
    email = mail.outbox[0]

    assert "URGENT: Financial Reconciliation Mismatch" in email.subject
    assert "User: tester@example.com" in email.body
    assert "Ledger Total: 100.00" in email.body
    assert "Wallet Cache: 500.00" in email.body
