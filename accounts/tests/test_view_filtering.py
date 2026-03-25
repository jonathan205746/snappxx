import pytest
from django.urls import reverse
from django.contrib.auth import get_user_model
from accounts.models import GroupJoinRequest, SavingsGroup

@pytest.mark.django_db
def test_my_joined_groups_excludes_pending_requests(auth_client, test_user):
    """
    UX/Logic: 'MyJoinedGroups' should only show groups where membership is APPROVED.
    Pending requests should not appear in this list.
    """
    # Create Group A (User is Admin/Member) - Should see this
    group_a = SavingsGroup.objects.create(
        admin=test_user,
        name="Group A",
        group_name="grp-a",
        contribution_amount=10,
        frequency='daily',
        expected_members=2,
        status='active',
        payout_timeline_days=30
    )

    # Create Group B (User has PENDING request) - Should NOT see this
    other_user = get_user_model().objects.create_user(username="other", email="o@t.com", password="password")
    group_b = SavingsGroup.objects.create(
        admin=other_user,
        name="Group B",
        group_name="grp-b",
        contribution_amount=10,
        frequency='daily',
        expected_members=2,
        status='active',
        payout_timeline_days=30
    )
    GroupJoinRequest.objects.create(user=test_user, group=group_b, status='pending')

    # Fetch List
    url = reverse('my-joined-groups')
    response = auth_client.get(url)

    assert response.status_code == 200

    # Check if response is paginated or direct list
    data = response.data
    if isinstance(data, dict) and 'results' in data:
        data = data['results']

    group_ids = [g['id'] for g in data]
