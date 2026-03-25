import pytest
from django.urls import reverse
from django.db import connection
from django.test.utils import CaptureQueriesContext
from accounts.models import GroupMembership

@pytest.mark.django_db
def test_dashboard_scaling_n_plus_one(auth_client, test_user, test_group):
    """
    Ensure the DashboardView query count is O(1) relative to the number of groups.
    Fetching 5 groups should take roughly the same queries as fetching 20.
    """
    url = reverse('dashboard')

    with CaptureQueriesContext(connection) as baseline_ctx:
        auth_client.get(url)
    baseline_count = len(baseline_ctx.captured_queries)

    new_groups = []
    memberships = []

    from accounts.models import SavingsGroup
    for i in range(19):
        g = SavingsGroup(
            admin=test_user,
            name=f"Perf Group {i}",
            group_name=f"perf-group-{i}",
            contribution_amount=10,
            frequency='daily',
            expected_members=5,
            status='active',
            payout_timeline_days=30
        )
        new_groups.append(g)

    SavingsGroup.objects.bulk_create(new_groups)

    saved_groups = SavingsGroup.objects.filter(group_name__startswith="perf-group-")
    for g in saved_groups:
        memberships.append(GroupMembership(user=test_user, group=g))

    GroupMembership.objects.bulk_create(memberships)

    with CaptureQueriesContext(connection) as scaled_ctx:
        auth_client.get(url)
    scaled_count = len(scaled_ctx.captured_queries)

    print(f"Baseline Queries: {baseline_count}, Scaled Queries: {scaled_count}")
    assert scaled_count <= baseline_count + 2, \
        f"N+1 Query detected! 1 group took {baseline_count}, 20 groups took {scaled_count}"
