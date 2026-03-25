import pytest
from django.utils import timezone
from datetime import timedelta
from accounts.models import SavingsGoal
from accounts.tasks import send_payout_notification_email_async, send_goal_reminders
from django.core import mail
from accounts.models import GoalContribution
from decimal import Decimal

@pytest.mark.django_db
def test_payout_notification_email_content(test_user, test_group):
    """
    Verify email includes correct amount, deep link, etc.
    """
    payout_amount = "500.00"
    send_payout_notification_email_async(test_user.id, test_group.id, 1, payout_amount)
    assert len(mail.outbox) == 1
    email = mail.outbox[0]
    assert "Payout Processed" in email.subject
    assert "₵500.00" in email.body
    assert test_user.profile.momo_number in email.body

@pytest.mark.django_db
def test_goal_reminder_email_for_overdue(test_user):
    """
    Verify that a reminder is sent for an overdue goal that is due for contribution
    (same messaging as normal reminders, but includes the past target date).
    """
    past_date = timezone.now().date() - timedelta(days=1)
    goal = SavingsGoal.objects.create(
        user=test_user,
        name="Overdue Goal Reminder Test",
        target_amount=Decimal('1000.00'),
        regular_contribution=Decimal('100.00'),
        frequency='daily',
        target_date=past_date,
        last_contribution_date=past_date - timedelta(days=2),
    )

    GoalContribution.objects.create(
        goal=goal,
        amount=Decimal('200.00'),
        is_verified=True,
    )

    send_goal_reminders()

    assert len(mail.outbox) == 1
    email = mail.outbox[0]

    # Standard reminder checks
    assert "Reminder" in email.subject
    assert goal.name in email.body
    assert "daily" in email.body.lower()
    assert "₵100.00" in email.body

    # Confirm it's for an overdue goal
    expected_date_str = past_date.strftime('%B %d, %Y')
    assert expected_date_str in email.body

    assert "20.0%" in email.body or "200.00" in email.body
