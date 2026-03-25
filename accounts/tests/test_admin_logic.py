import pytest
from django.urls import reverse
from unittest.mock import patch
from accounts.models import GroupJoinRequest, PayoutOrder

@pytest.mark.django_db
class TestGroupAdminLogic:

    def test_reject_request_does_not_fill_spot(self, auth_client, test_group, test_user):
        """Rejecting a user should not increment current_members."""
        # 1. Setup Request
        from django.contrib.auth import get_user_model
        User = get_user_model()
        applicant = User.objects.create_user(username="applicant", email="app@test.com", password="pw")

        req = GroupJoinRequest.objects.create(user=applicant, group=test_group, status='pending')

        # 2. Admin Action: Reject
        url = reverse('group-request-action', kwargs={'pk': req.id})

        # Mock email task
        with patch('accounts.views.send_group_join_response_email_async.delay'):
            response = auth_client.post(url, {'action': 'reject'})

        assert response.status_code == 200

        # 3. Verify State
        test_group.refresh_from_db()
        assert test_group.current_members == 1  # Only admin remains

        req.refresh_from_db()
        assert req.status == 'rejected'

    def test_group_auto_activation_on_full(self, auth_client, test_group, test_user):
        """
        Critical: When the last member is approved, the group must:
        1. Set start_date to today.
        2. Generate PayoutOrder records.
        """
        # Ensure Admin is actually a member first
        from accounts.models import GroupMembership
        GroupMembership.objects.get_or_create(user=test_user, group=test_group)

        # Setup group needing 2 members, currently has 1 (admin)
        test_group.expected_members = 2
        test_group.current_members = 1
        test_group.start_date = None
        test_group.save()

        # Create Applicant
        from django.contrib.auth import get_user_model
        User = get_user_model()
        applicant = User.objects.create_user(username="last_person", email="last@test.com", password="pw")
        req = GroupJoinRequest.objects.create(user=applicant, group=test_group, status='pending')

        # Approve
        url = reverse('group-request-action', kwargs={'pk': req.id})
        with patch('accounts.views.send_group_join_response_email_async.delay'):
            auth_client.post(url, {'action': 'approve'})

        # Assertions
        test_group.refresh_from_db()
        assert test_group.current_members == 2
        assert test_group.start_date is not None

        # Check Payout Orders were created
        # Admin is usually first joined, Applicant second
        orders = PayoutOrder.objects.filter(group=test_group).order_by('position')
        assert orders.count() == 2
        assert orders[0].membership.user == test_group.admin
        assert orders[1].membership.user == applicant
