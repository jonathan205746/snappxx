import pytest
from decimal import Decimal
from django.core import mail
from datetime import timedelta
from unittest.mock import patch
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from accounts.tasks import send_goal_reminders
from dateutil.relativedelta import relativedelta
from accounts.models import SavingsGoal, Contribution, GroupJoinRequest

@pytest.mark.django_db
class TestGoalReminders:

    def test_goal_is_due_logic(self, test_user):
        """
        Verify the custom date math for goal due dates.
        """
        today = timezone.now().date()

        # Monthly Goal: Last paid exactly 1 month ago -> DUE
        goal_monthly = SavingsGoal.objects.create(
            user=test_user, name="Monthly", target_amount=1000,
            regular_contribution=100, frequency='monthly',
            target_date=today + timedelta(days=60),
            last_contribution_date=today - relativedelta(months=1)
        )
        assert goal_monthly.is_due is True

        # Monthly Goal: Last paid 20 days ago -> NOT DUE
        goal_monthly.last_contribution_date = today - timedelta(days=20)
        goal_monthly.save()
        # Refresh computed property
        goal_monthly.refresh_from_db()
        assert goal_monthly.is_due is False

        # Weekly Goal: Last paid 7 days ago -> DUE
        goal_weekly = SavingsGoal.objects.create(
            user=test_user, name="Weekly", target_amount=1000,
            regular_contribution=100, frequency='weekly',
            target_date=today + timedelta(days=60),
            last_contribution_date=today - timedelta(days=7)
        )
        assert goal_weekly.is_due is True

    def test_reminder_task_respects_cooldown(self, test_user):
        """
        Ensure the reminder task doesn't spam users if they were
        reminded less than 24 hours ago.
        """
        from accounts.models import GoalContribution

        # Ensure user is verified
        test_user.is_verified = True
        test_user.save()

        today = timezone.now().date()

        # Setup: Goal is DUE (last paid 2 months ago)
        # But it was REMINDED 1 hour ago
        goal = SavingsGoal.objects.create(
            user=test_user,
            name="Spam Check",
            target_amount=Decimal("1000.00"),
            regular_contribution=Decimal("100.00"),
            frequency='monthly',
            target_date=today + timedelta(days=60),
            last_contribution_date=today - relativedelta(months=2),
            last_reminded_at=timezone.now() - timedelta(hours=1)
        )

        # Add a verified contribution to ensure the goal is "Active"
        # Some logic requires current_saved > 0 but < target to be "In Progress"
        GoalContribution.objects.create(
            goal=goal,
            amount=Decimal("50.00"),
            is_verified=True
        )

        # Run Task (Cooldown phase)
        # Clear outbox first to be sure
        mail.outbox = []
        send_goal_reminders()

        # Assert: No email sent because of cooldown (1 hour < 24 hours)
        assert len(mail.outbox) == 0

        # Advance reminded_at to 25 hours ago
        SavingsGoal.objects.filter(id=goal.id).update(
            last_reminded_at=timezone.now() - timedelta(hours=25)
        )

        # Run Task Again
        send_goal_reminders()

        # Assert: Email sent now that cooldown is over
        assert len(mail.outbox) == 1
        assert "Reminder" in mail.outbox[0].subject or "contribution" in mail.outbox[0].subject.lower()

@pytest.mark.django_db
class TestDashboardMath:

    def test_growth_percentage_calculation(self, auth_client, test_user, funded_wallet, test_group, membership):
        """
        Verify the 'Growth' logic:
        ((Current - Previous) / Previous) * 100
        """
        from accounts.models import LedgerEntry

        # Setup Dates
        today = timezone.now()
        two_months_ago = today - relativedelta(months=2)

        # Create "Previous Period" Savings (2 Months ago)
        # Create a verified contribution in the past
        past_contrib = Contribution.objects.create(
            membership=membership,
            amount=Decimal("100.00"),
            cycle_number=1,
            is_verified=True
        )
        # Hack the timestamp (auto_now_add usually blocks this, so we update)
        Contribution.objects.filter(id=past_contrib.id).update(paid_at=two_months_ago)

        # Create corresponding Ledger Entry for "Current Total" calculation
        LedgerEntry.objects.create(
            wallet=funded_wallet, amount=Decimal("100.00"),
            direction='credit', transaction_type='contribution',
            reference="PAST-1", description="Past"
        )

        # Create "Current Period" Savings (Today)
        # Add another 50 (Total now 150)
        Contribution.objects.create(
            membership=membership,
            amount=Decimal("50.00"),
            cycle_number=2,
            is_verified=True
        )
        LedgerEntry.objects.create(
            wallet=funded_wallet, amount=Decimal("50.00"),
            direction='credit', transaction_type='contribution',
            reference="NOW-1", description="Now"
        )

        url = reverse('dashboard')
        response = auth_client.get(url)

        assert response.status_code == 200
        assert response.data['growth_percentage'] == 50.0
        assert "+50.0%" in response.data['growth_text']


@pytest.mark.django_db
class TestJoinRequestFlow:

    def test_resubmit_rejected_request(self, auth_client, test_group, test_user):
        """
        Verify that a user who was REJECTED can fix their application
        and RE-SUBMIT it.
        """
        # Setup: Existing Rejected Request
        from django.contrib.auth import get_user_model
        User = get_user_model()
        # Create a distinct user for the applicant
        applicant = User.objects.create_user(username="applicant", email="app@test.com", password="pw")

        req = GroupJoinRequest.objects.create(
            user=applicant,
            group=test_group,
            status='rejected'
        )

        # Authenticate as Applicant
        client = APIClient()
        client.force_authenticate(user=applicant)

        # Submit Join Request Again
        url = reverse('group-request-join', kwargs={'group_id': test_group.id})
        data = {"reason": "I have fixed my KYC documents."}

        # Mock the email task
        with patch('accounts.views.send_group_join_request_email_async.delay') as mock_email:
            response = client.post(url, data, HTTP_X_IDEMPOTENCY_KEY="resubmit-001")

        # Assertions
        assert response.status_code == 200
        assert "re-submitted" in response.data['message']

        req.refresh_from_db()
        assert req.status == 'pending'
        assert req.reason == "I have fixed my KYC documents."

        # Verify Admin was notified
        assert mock_email.called
