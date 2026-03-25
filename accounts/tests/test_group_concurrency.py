import pytest
import uuid
import concurrent.futures
from unittest.mock import patch
from django.urls import reverse
from django.contrib.auth import get_user_model
from accounts.models import GroupJoinRequest

User = get_user_model()

@pytest.mark.django_db(transaction=True)
def test_prevent_overfilling_group(api_client, test_user, test_group):
    """
    Simulate a race condition where 5 users try to fill the LAST slot
    at the exact same time. Only 1 should succeed.
    """
    # Setup: Create a group with 1 slot remaining (Total capacity 2)
    test_group.expected_members = 2
    test_group.current_members = 1
    test_group.save()

    # Create 5 racers and their join requests directly in DB
    requests = []
    for i in range(5):
        unique_id = uuid.uuid4().hex[:4]
        u = User.objects.create_user(
            username=f"racer_{i}_{unique_id}",
            email=f"racer_{i}_{unique_id}@test.com",
            password="pass"
        )
        req = GroupJoinRequest.objects.create(user=u, group=test_group, status='pending')
        requests.append(req)

    # Define the approval logic
    def approve_request(req_id):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=test_group.admin)
        url = reverse('group-request-action', kwargs={'pk': req_id})
        # Use unique idempotency keys so the decorator doesn't block different users
        return client.post(
            url,
            {'action': 'approve'},
            HTTP_X_IDEMPOTENCY_KEY=f"race-key-{req_id}"
        )

    # Attack! Run 5 approvals in parallel.
    with patch('accounts.views.send_group_join_response_email_async.delay') as mock_email:
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(approve_request, r.id) for r in requests]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # Verify Results
    success_count = sum(1 for r in results if r.status_code == 200)
    failure_count = sum(1 for r in results if r.status_code == 400)

    test_group.refresh_from_db()

    # The Logic: Only 1 person should have gotten the 1 remaining slot.
    assert success_count == 1
    assert failure_count == 4
    assert test_group.current_members == 2
