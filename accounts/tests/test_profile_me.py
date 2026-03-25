import pytest
from django.urls import reverse
from rest_framework import status
from accounts.serializers import ProfileSerializer

@pytest.mark.django_db
def test_me_view_for_unverified_user(auth_client, test_user):
    """
    Edge: Unverified users should get 401 or limited data? (Assumes 200 with is_verified=False)
    """
    test_user.is_verified = False
    test_user.save()
    url = reverse('me')
    response = auth_client.get(url)
    assert response.status_code == status.HTTP_200_OK
    assert response.data['user']['is_verified'] is False

@pytest.mark.django_db
def test_profile_serializer_handles_nulls(test_user):
    """
    Edge: Serialize with missing fields (e.g., no picture).
    """
    # Remove picture
    test_user.profile.profile_picture = None
    test_user.profile.save()
    serializer = ProfileSerializer(instance=test_user.profile)
    data = serializer.data
    assert data['profile_picture'] is None
