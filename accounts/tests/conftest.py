import pytest
import datetime
from decimal import Decimal
from unittest.mock import MagicMock
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from accounts.models import SavingsGroup, Profile, GroupMembership

User = get_user_model()

@pytest.fixture
def api_client():
    """Custom API client for integration tests."""
    return APIClient()

@pytest.fixture
def test_user(db):
    """
    A standard verified user.
    Uses get_or_create to prevent IntegrityErrors when mixing transaction=True tests.
    """
    user, created = User.objects.get_or_create(
        username="test_tester",
        defaults={
            "email": "tester@example.com",
            "is_verified": True
        }
    )

    if created:
        user.set_password("password123")
        user.save()

    # Ensure Profile exists
    Profile.objects.update_or_create(
        user=user,
        defaults={
            "full_name": "Test User",
            "date_of_birth": datetime.date(1990, 1, 1),
            "user_type": "individual",
            "momo_number": "0244123456", # Ensure valid number
            "momo_provider": "mtn"
        }
    )

    user.refresh_from_db()
    return user

@pytest.fixture
def auth_client(api_client, test_user):
    """A client pre-authenticated with a JWT token."""
    api_client.force_authenticate(user=test_user)
    return api_client

@pytest.fixture
def test_group(db, test_user):
    """
    Creates a savings group.
    Uses get_or_create to prevent unique constraint violations on group_name.
    """
    group, created = SavingsGroup.objects.get_or_create(
        group_name="business-growth-unique-id",
        defaults={
            "admin": test_user,
            "name": "Business Growth 2026",
            "description": "Saving for inventory",
            "contribution_amount": Decimal("100.00"),
            "frequency": "monthly",
            "payout_timeline_days": 30,
            "expected_members": 5,
            "status": "active"
        }
    )
    return group

@pytest.fixture
def funded_wallet(test_user):
    """Ensures the test user has a wallet with a starting balance."""
    # Wallet is auto-created by signals, so we just get it
    wallet = test_user.wallet
    wallet.current_balance = Decimal("500.00")
    wallet.save()
    return wallet

@pytest.fixture
def membership(db, test_user, test_group):
    """Officially joins the test_user to the test_group."""
    membership, created = GroupMembership.objects.get_or_create(
        user=test_user,
        group=test_group,
        defaults={
            "joined_at": datetime.datetime.now()
        }
    )
    return membership


@pytest.fixture
def mock_cloudinary_upload(monkeypatch):
    """
    Automatically blocks actual network calls to Cloudinary for ALL tests.
    Returns a fake URL so models save successfully.
    """
    mock_upload = MagicMock(return_value={
        'public_id': 'test_public_id',
        'secure_url': 'https://res.cloudinary.com/demo/image/upload/test.jpg'
    })
    monkeypatch.setattr("cloudinary.uploader.upload", mock_upload)
    return mock_upload
