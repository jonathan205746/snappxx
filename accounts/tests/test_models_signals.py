import pytest
from decimal import Decimal
from django.db import IntegrityError
from django.contrib.auth import get_user_model
from accounts.models import Wallet, SavingsGroup, Profile

User = get_user_model()

@pytest.mark.django_db
def test_group_wallet_signal_on_create(test_user):
    """
    Verify signal creates wallet for new groups.
    """
    group = SavingsGroup.objects.create(
        admin=test_user, name="Signal Test", group_name="signal-group-unique-123",
        contribution_amount=100, frequency='daily', expected_members=2,
        status='pending', payout_timeline_days=30
    )
    assert Wallet.objects.filter(group=group).exists()
    wallet = Wallet.objects.get(group=group)
    assert wallet.current_balance == Decimal('0.00')

@pytest.mark.django_db
def test_profile_momo_hash_uniqueness():
    """
    Different formats for same number must collide on hash (unique violation).
    Use fresh users to avoid conflict with shared test_user fixture.
    """
    user1 = User.objects.create_user(
        username="hash_test_user1",
        email="hash1@example.com",
        password="pass123"
    )
    Profile.objects.create(
        user=user1,
        full_name="Test1",
        date_of_birth="1990-01-01",
        user_type="individual",
        ghana_post_address="GA-123-4567",
        momo_provider="mtn",
        momo_number="+233244123456",
        momo_name="Test"
    )

    user2 = User.objects.create_user(
        username="hash_test_user2",
        email="hash2@example.com",
        password="pass123"
    )
    with pytest.raises(IntegrityError) as excinfo:
        Profile.objects.create(
            user=user2,
            full_name="Test2",
            date_of_birth="1990-01-01",
            user_type="individual",
            ghana_post_address="GA-123-4567",
            momo_provider="mtn",
            momo_number="0244123456",
            momo_name="Test"
        )
    assert "accounts_profile_momo_number_hash_key" in str(excinfo.value)
