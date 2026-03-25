import pytest
from datetime import timedelta
from unittest.mock import patch
from django.utils import timezone
from accounts.models import SavingsGroup
from accounts.tasks import process_daily_payouts

@pytest.mark.django_db
def test_orchestrator_selects_correct_groups(test_user):
    today = timezone.now().date()


    group_due = SavingsGroup.objects.create(
        admin=test_user, name="Due Group", group_name="due-group",
        contribution_amount=10, frequency='daily', expected_members=2,
        status='active',
        start_date=today - timedelta(days=1),
        payout_interval_days=1,
        payout_timeline_days=30
    )


    group_not_due = SavingsGroup.objects.create(
        admin=test_user, name="Not Due", group_name="not-due-group",
        contribution_amount=10, frequency='monthly', expected_members=2,
        status='active',
        start_date=today - timedelta(days=1),
        payout_interval_days=30,
        payout_timeline_days=30
    )

    group_inactive = SavingsGroup.objects.create(
        admin=test_user, name="Inactive", group_name="inactive-group",
        contribution_amount=10, frequency='daily', expected_members=2,
        status='pending',
        start_date=today - timedelta(days=1),
        payout_interval_days=1,
        payout_timeline_days=30
    )

    with patch('accounts.tasks.execute_group_payout.delay') as mock_payout:
        result_msg = process_daily_payouts()

        assert "Dispatched 1 payout tasks" in result_msg

        mock_payout.assert_called_once_with(group_due.id)

        called_args = [arg[0][0] for arg in mock_payout.call_args_list]
        assert group_not_due.id not in called_args
        assert group_inactive.id not in called_args
