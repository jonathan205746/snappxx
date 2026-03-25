import io
import pytest
from PIL import Image
from accounts.serializers import GroupAdminKYCSerializer
from django.core.files.uploadedfile import SimpleUploadedFile

# Define a minimal mock request for context
class MockRequest:
    def __init__(self, user):
        self.user = user

def create_valid_image(size=(100, 100), format='JPEG'):
    """Helper to generate valid image bytes."""
    file = io.BytesIO()
    image = Image.new('RGB', size, color='white')
    image.save(file, format=format)
    file.seek(0)
    return file.read()

@pytest.mark.django_db
def test_kyc_rejects_large_files(test_user):
    """Ensure files > 5MB are rejected by the serializer."""

    # Create a valid small image header
    valid_image_data = create_valid_image()

    large_content = valid_image_data + b'\x00' * (6 * 1024 * 1024)

    large_file = SimpleUploadedFile(
        "big_id.jpg",
        large_content,
        content_type="image/jpeg"
    )

    data = {
        "ghana_card_front": large_file,
        "ghana_card_back": large_file,
        "live_photo": large_file
    }

    serializer = GroupAdminKYCSerializer(
        data=data,
        context={'request': MockRequest(test_user)}
    )

    # Assert validation fails specifically on file size
    assert not serializer.is_valid()
    assert "File size too large" in str(serializer.errors['ghana_card_front'])

@pytest.mark.django_db
def test_kyc_rejects_invalid_extensions(test_user):
    """Ensure files with unsupported extensions (even if valid images) are caught."""

    # Create a valid image (e.g., a GIF or BMP)
    # The validator only allows .jpg, .jpeg, .png
    bmp_content = create_valid_image(format='BMP')

    # Upload it with a .bmp extension
    bad_ext_file = SimpleUploadedFile(
        "test.bmp",
        bmp_content,
        content_type="image/bmp"
    )

    data = {
        "ghana_card_front": bad_ext_file,
        "ghana_card_back": bad_ext_file,
        "live_photo": bad_ext_file
    }

    serializer = GroupAdminKYCSerializer(
        data=data,
        context={'request': MockRequest(test_user)}
    )

    assert not serializer.is_valid()
    assert "Unsupported file format" in str(serializer.errors['ghana_card_front'])
