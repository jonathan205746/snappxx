import pytest
from decimal import Decimal
from django.utils import timezone
from django.contrib.auth import get_user_model
from accounts.tasks import execute_group_payout
from accounts.models import PayoutOrder, GroupMembership, Contribution, Profile

User = get_user_model()

@pytest.mark.django_db
def test_payout_execution(test_group, test_user, membership):
    """
    Test that the payout task:
    1. Identifies the correct beneficiary.
    2. Moves money from Group Wallet -> User Wallet.
    3. Advances the cycle number from 1 to 2.
    """
    test_group.expected_members = 2
    test_group.current_members = 2
    test_group.current_cycle_number = 1
    test_group.start_date = timezone.now().date()
    test_group.save()

    another_user = User.objects.create_user(
        email="pierres@test.com",
        username="prosper",
        password="pass1234"
    )
    Profile.objects.create(
        user=another_user,
        full_name="Another User",
        date_of_birth="1990-01-01",
        user_type="individual",
        ghana_post_address="GA-123-4567",
        momo_provider="mtn",
        momo_number="0244123457",
        momo_name="Another"
    )
    another_membership = GroupMembership.objects.create(
        user=another_user,
        group=test_group
    )

    PayoutOrder.objects.create(
        group=test_group,
        membership=membership,
        position=1
    )
    PayoutOrder.objects.create(
        group=test_group,
        membership=another_membership,
        position=2
    )

    Contribution.objects.create(
        membership=membership,
        amount=test_group.contribution_amount,
        cycle_number=1,
        is_verified=True
    )
    Contribution.objects.create(
        membership=another_membership,
        amount=test_group.contribution_amount,
        cycle_number=1,
        is_verified=True
    )

    pot_amount = test_group.contribution_amount * test_group.expected_members
    group_wallet = test_group.group_wallet
    group_wallet.current_balance = pot_amount
    group_wallet.save()

    user_wallet = test_user.wallet
    assert user_wallet.current_balance == Decimal('0.00')

    execute_group_payout(test_group.id)

    test_group.refresh_from_db()
    assert test_group.current_cycle_number == 2
    user_wallet.refresh_from_db()
    assert user_wallet.current_balance == pot_amount
    group_wallet.refresh_from_db()
    assert group_wallet.current_balance == Decimal('0.00')
