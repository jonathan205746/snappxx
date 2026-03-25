import pytest
from django.urls import reverse
from unittest.mock import patch
from rest_framework import status

@pytest.mark.django_db
def test_idempotency_prevents_duplicate_signup(api_client):
    url = reverse('signup')
    data = {
        "email": "unique@test.com",
        "username": "unique_tester",
        "password": "password123", "password2": "password123",
        "full_name": "Test User", "date_of_birth": "1990-01-01",
        "user_type": "individual", "ghana_post_address": "GA-123-4567",
        "momo_provider": "mtn", "momo_number": "0240000000", "momo_name": "Test Account"
    }
    # Headers must use the exact format the decorator expects
    headers = {'HTTP_X_IDEMPOTENCY_KEY': 'fixed-key-123'}

    with patch("accounts.views.send_dawurobo_otp_async") as mock_otp:
        mock_otp.return_value = {"success": True}

        # First Request
        response1 = api_client.post(url, data, **headers)
        assert response1.status_code == status.HTTP_201_CREATED, f"First request failed: {response1.data}"

        # Second Request (Replay Attack)
        response2 = api_client.post(url, data, **headers)

        # Should return 201 (cached response), NOT 400 or 500
        assert response2.status_code == status.HTTP_201_CREATED
        assert response2.data == response1.data  # Body must be identical

        # Verification: The SMS function should have only been called ONCE
        # despite us making two API calls.
        assert mock_otp.call_count == 1
