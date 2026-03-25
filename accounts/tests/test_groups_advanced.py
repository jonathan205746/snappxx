import pytest
from django.urls import reverse
from unittest.mock import patch
from rest_framework import status
from accounts.models import PayoutOrder, GroupJoinRequest

from django.contrib.auth import get_user_model

User = get_user_model()

@pytest.mark.django_db
def test_group_activation_generates_payout_orders(auth_client, test_group, test_user, membership):
    """
    Verify auto-activation creates PayoutOrders based on joined_at.
    """
    PayoutOrder.objects.filter(group=test_group).delete()

    # Setup: Group needs 2 members, has 1 (admin)
    test_group.expected_members = 2
    test_group.current_members = 1
    test_group.start_date = None
    test_group.save()

    # Create applicant and pending request
    applicant = User.objects.create_user(email="<app@test.com>", username="applicant_test")
    req = GroupJoinRequest.objects.create(user=applicant, group=test_group, status='pending')

    # Mock the async email task to prevent any external issues
    with patch('accounts.views.send_group_join_response_email_async.delay') as mock_email:
        url = reverse('group-request-action', kwargs={'pk': req.id})
        response = auth_client.post(url, {'action': 'approve'})

    assert response.status_code == status.HTTP_200_OK
    assert mock_email.called

    test_group.refresh_from_db()
    assert test_group.current_members == 2
    assert test_group.start_date is not None

    orders = PayoutOrder.objects.filter(group=test_group).order_by('position')
    assert orders.count() == 2
    assert orders[0].membership.user == test_user
    assert orders[1].membership.user == applicant


@pytest.mark.django_db
def test_cannot_contribute_to_suspended_group(auth_client, test_group):
    """
    Edge: Suspended groups block contributions.
    """
    test_group.status = 'suspended'
    test_group.save()
    url = reverse('group-contribute', kwargs={'group_id': test_group.id})
    response = auth_client.post(url, HTTP_X_IDEMPOTENCY_KEY="suspend-test")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "Group not found or not active" in str(response.data)
