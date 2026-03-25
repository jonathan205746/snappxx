import pytest
from unittest.mock import patch
from django.urls import reverse
from rest_framework import status
from accounts.models import Profile
from django.contrib.auth import get_user_model

User = get_user_model()

@pytest.mark.django_db
def test_signup_atomic_rollback_on_otp_provider_failure(api_client):
    """
    Resilience Test: If the Critical Infrastructure (SMS Provider) fails,
    the entire User creation must be rolled back.
    """
    url = reverse('signup')
    data = {
        "email": "prosper@test.com",
        "username": "rollback_user",
        "password": "password123", "password2": "password123",
        "full_name": "Rollback User", "date_of_birth": "1990-01-01",
        "user_type": "individual", "ghana_post_address": "GA-123-4567",
        "momo_provider": "mtn", "momo_number": "0245555555", "momo_name": "Test"
    }

    # Simulate Cloudinary working fine
    with patch("cloudinary.uploader.upload") as mock_upload:
        mock_upload.return_value = {"secure_url": "http://img.com/1.jpg"}

        # SIMULATE FAILURE: The SMS Provider returns success=False
        with patch("accounts.views.send_dawurobo_otp_async") as mock_otp:
            mock_otp.return_value = {"success": False}

            response = api_client.post(
                url,
                data,
                format='multipart',
                HTTP_X_IDEMPOTENCY_KEY="rollback-test-key-123"
            )

    # Assert Failure Response (Server Error 500)
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Registration failed" in str(response.data)

    # CRITICAL: Assert Atomicity
    # The User should NOT exist in the DB because the transaction rolled back
    assert not User.objects.filter(email="prosper@test.com").exists()
    assert not Profile.objects.filter(momo_number_hash__isnull=False).exists()
