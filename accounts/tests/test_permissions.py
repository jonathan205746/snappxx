import pytest
from django.urls import reverse
from rest_framework import status
from accounts.models import Profile
from datetime import date

@pytest.mark.django_db
class TestPermissionBoundaries:

    def test_horizontal_privilege_escalation(self, api_client, test_user):
        """User A should not see User B's dashboard."""
        from django.contrib.auth import get_user_model
        User = get_user_model()

        user_b = User.objects.create(email="userb@example.com", username="userb")
        Profile.objects.filter(user=user_b).update(
            full_name="User B",
            date_of_birth=date(1990, 1, 1),
            momo_number="0240000001"
        )

        api_client.force_authenticate(user=test_user)
        url = reverse('dashboard')
        response = api_client.get(url)

        assert response.status_code == 200
        assert "userb@example.com" not in str(response.content)

    def test_kyc_status_lock(self, api_client, test_user, test_group, membership, funded_wallet):
        """Unverified users should be blocked from contributing to Savings Groups."""
        test_user.is_verified = False
        test_user.save()
        test_user.refresh_from_db()
        api_client.force_authenticate(user=test_user)
        try:
            url = reverse('group-contribute', kwargs={'group_id': test_group.id})
        except:
            url = f"/api/accounts/groups/{test_group.id}/contribute/"
        response = api_client.post(url, {"amount": "100.00"}, HTTP_X_IDEMPOTENCY_KEY="key-kyc")
        assert response.status_code == status.HTTP_403_FORBIDDEN
