import pytest
from django.contrib.auth import get_user_model
from accounts.models import Wallet

User = get_user_model()

@pytest.mark.django_db
def test_user_wallet_creation_on_signup():
    """
    Critical Test: Ensure signals.py correctly initializes
    a wallet when a new user is saved.
    """
    user = User.objects.create_user(
        username="testuser_1",
        email="newuser@snappx.com",
        password="securepassword"
    )

    # Check if wallet exists
    wallet_exists = Wallet.objects.filter(user=user).exists()
    assert wallet_exists is True

    # Check initial balance is exactly 0.00
    wallet = Wallet.objects.get(user=user)
    assert float(wallet.current_balance) == 0.0
