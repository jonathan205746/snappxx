import pytest
from django.urls import reverse
from unittest.mock import patch
from rest_framework import status

@pytest.mark.django_db
class TestAuthSecurity:

    def test_verify_otp_rejects_invalid_code(self, api_client, test_user):
        """Ensure incorrect OTPs do not verify the user."""
        test_user.is_verified = False
        test_user.save()

        url = reverse('otp-verify')
        data = {
            "phone_number": "0244123456",
            "code": "WRONG1"
        }

        # Mock the synchronous verification to return False
        with patch("accounts.views.verify_and_invalidate_otp_sync") as mock_verify:
            mock_verify.return_value = False

            response = api_client.post(url, data)

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "Invalid or expired OTP" in str(response.data)

            test_user.refresh_from_db()
            assert test_user.is_verified is False

    def test_password_reset_flow_security(self, api_client, test_user):
        """Test that password reset requires a valid OTP and updates the password."""
        url = reverse('reset_password')
        data = {
            "phone": "0244123456",
            "code": "123456",
            "password": "newpassword123",
            "password2": "newpassword123"
        }

        # Mock successful OTP verification
        with patch("accounts.views.verify_and_invalidate_otp_sync") as mock_verify:
            mock_verify.return_value = True

            response = api_client.post(url, data)

            assert response.status_code == 200
            assert "Password reset successful" in str(response.data)

            # Verify login with new password works
            test_user.refresh_from_db()
            assert test_user.check_password("newpassword123") is True

    def test_login_requires_verified_account(self, api_client, test_user):
        """Unverified users should be blocked from logging in."""
        test_user.is_verified = False
        test_user.set_password("password123")
        test_user.save()

        url = reverse('login')
        data = {
            "login_field": test_user.email,
            "password": "password123"
        }

        response = api_client.post(url, data)

        # Should fail with 401
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Account not verified" in str(response.data)
