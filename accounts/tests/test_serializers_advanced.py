import jwt
import pytest
from datetime import datetime
from accounts.serializers import CustomTokenObtainPairSerializer, SavingsGroupCreateSerializer
from django.test.client import RequestFactory
from accounts.models import GroupAdminKYC

@pytest.mark.django_db
def test_custom_token_serializer_remember_me(test_user):
    """
    Verify remember_me in serializer affects token claims if needed.
    """
    serializer = CustomTokenObtainPairSerializer(data={
        "login_field": test_user.email,
        "password": "password123",
        "remember_me": True
    })
    assert serializer.is_valid(), serializer.errors
    tokens = serializer.validated_data
    payload = jwt.decode(tokens['refresh'], options={"verify_signature": False})
    exp_date = datetime.fromtimestamp(payload['exp'])
    now = datetime.now()
    duration_days = (exp_date - now).days

    assert duration_days > 30, f"Token lifetime not extended: only {duration_days} days"

@pytest.mark.django_db
def test_group_create_serializer_skips_kyc_if_exists(test_user):
    """
    When kyc_exists, required fields are skipped.
    """
    GroupAdminKYC.objects.create(user=test_user)  # Exists
    factory = RequestFactory()
    request = factory.post('/groups/create/')
    request.user = test_user
    serializer = SavingsGroupCreateSerializer(context={'request': request}, data={
        "group_name": "No KYC Needed",
        "contribution_amount": 100,
        "frequency": "daily",
        "payout_timeline_days": 1,
        "expected_members": 2,
        "description": "Test"
    })
    assert serializer.is_valid(), serializer.errors
    assert 'kyc' not in serializer.validated_data
