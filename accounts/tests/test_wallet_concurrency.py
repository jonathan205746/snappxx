import pytest
import concurrent.futures
from decimal import Decimal
from django.urls import reverse
from rest_framework.test import APIClient
from accounts.models import GroupMembership, SavingsGroup

@pytest.mark.django_db(transaction=True)
def test_prevent_wallet_double_spend(test_user, test_group, funded_wallet, membership):
    """
    Simulate a 'Double-Spend' attack across TWO groups.
    Wallet Balance: 100.
    Group A Cost: 60.
    Group B Cost: 60.
    Total Needed: 120.
    Outcome: Only 1 succeeds. The 2nd must fail with 'Insufficient wallet balance'.
    """
    # Setup Wallet (100 GHS)
    funded_wallet.current_balance = Decimal("100.00")
    funded_wallet.save()

    # Setup Group A (The default test_group, Cost 60)
    test_group.contribution_amount = Decimal("60.00")
    test_group.save()

    # Setup Group B (A second group, Cost 60)
    group_b = SavingsGroup.objects.create(
        admin=test_user,
        name="Group B",
        group_name="group-b-unique",
        contribution_amount=Decimal("60.00"),
        frequency='daily',
        expected_members=5,
        status='active',
        payout_timeline_days=30
    )
    # Join user to Group B
    GroupMembership.objects.create(user=test_user, group=group_b)

    # Define the attack
    # Target two different URLs to bypass the "Cycle Limit" check
    url_a = reverse('group-contribute', kwargs={'group_id': test_group.id})
    url_b = reverse('group-contribute', kwargs={'group_id': group_b.id})

    targets = [url_a, url_b]

    def attempt_contribution(index):
        client = APIClient()
        client.force_authenticate(user=test_user)
        return client.post(
            targets[index],
            {},
            HTTP_X_IDEMPOTENCY_KEY=f"attack-key-{index}"
        )

    # Execute Concurrent Attack
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        # Submit both requests at once
        futures = [executor.submit(attempt_contribution, i) for i in range(2)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # Assertions
    successes = [r for r in results if r.status_code == 201]
    failures = [r for r in results if r.status_code == 400]

    assert len(successes) == 1
    assert len(failures) == 1
    # Now we confirm it failed for the RIGHT reason (Money, not Cycle limits)
    assert "Insufficient wallet balance" in str(failures[0].data)

    # Verify final balance is 40.00 (100 - 60)
    funded_wallet.refresh_from_db()
    assert funded_wallet.current_balance == Decimal("40.00")
