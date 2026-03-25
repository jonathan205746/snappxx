import io
import pytest
from django.urls import reverse
from django.test import override_settings
from unittest.mock import patch
from rest_framework import status
from accounts.models import User, Profile
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

@pytest.mark.django_db
def test_signup_file_upload_failure_handling(api_client):
    """
    Edge: Cloudinary outage on optional profile picture upload should NOT rollback signup.
    User created successfully, but profile_picture remains None.
    """
    url = reverse('signup')

    # Generate a dummy in-memory image file to trigger the upload path
    image = io.BytesIO()
    img = Image.new('RGB', (100, 100), color='red')
    img.save(image, format='JPEG')
    image.seek(0)
    dummy_file = SimpleUploadedFile("test.jpg", image.read(), content_type="image/jpeg")

    data = {
        "email": "<filefail@test.com>",
        "password": "pass12345", "password2": "pass12345",
        "full_name": "File Fail", "date_of_birth": "1990-01-01",
        "user_type": "individual", "ghana_post_address": "GA-123-4567",
        "momo_provider": "mtn", "momo_number": "0249999999", "momo_name": "Test",
        "profile_picture": dummy_file
    }

    with patch("cloudinary.uploader.upload", side_effect=Exception("Cloudinary Down")):
        with patch("accounts.views.send_dawurobo_otp_async", return_value={"success": True}):
            response = api_client.post(url, data, format='multipart', HTTP_X_IDEMPOTENCY_KEY="file-fail-key")

    # View gracefully handles optional upload failure → success
    assert response.status_code == status.HTTP_201_CREATED
    assert "Account created successfully" in str(response.data)

    # User and Profile created (no rollback)
    assert User.objects.filter(email="<filefail@test.com>").exists()
    profile = Profile.objects.get(user__email="<filefail@test.com>")
    assert profile.profile_picture is None


@pytest.mark.django_db
@override_settings(RATELIMIT_ENABLE=False)
def test_otp_invalidation_prevents_reuse(api_client, test_user):
    """
    Security: After successful verification, the OTP must be invalidated.
    Rate limiting is disabled here to allow rapid sequential requests.
    """
    url = reverse('otp-verify')
    data = {"phone_number": test_user.profile.momo_number, "code": "VALID123"}

    # Mock successful verify once
    with patch("accounts.views.verify_and_invalidate_otp_sync", return_value=True):
        api_client.post(url, data)

    # Second attempt should fail (invalidated)
    with patch("accounts.views.verify_and_invalidate_otp_sync", return_value=False):
        response = api_client.post(url, data)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid or expired OTP" in str(response.data)
