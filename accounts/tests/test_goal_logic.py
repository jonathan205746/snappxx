import pytest
from decimal import Decimal
from django.urls import reverse
from rest_framework import status
from django.utils import timezone
from datetime import timedelta, date
from accounts.models import SavingsGoal, GoalContribution
from accounts.serializers import SavingsGoalCreateSerializer


@pytest.mark.django_db
class TestGoalLogic:

    def test_cannot_create_goal_in_past(self, test_user):
        """Serializer validation should catch past dates."""
        yesterday = timezone.now().date() - timedelta(days=1)
        data = {
            "name": "Past Goal",
            "target_amount": Decimal('1000.00'),
            "regular_contribution": Decimal('100.00'),
            "target_date": yesterday,
            "frequency": "monthly"
        }

        # We need a mock request for the serializer context
        class MockReq:
            user = test_user

        serializer = SavingsGoalCreateSerializer(data=data, context={'request': MockReq()})
        assert not serializer.is_valid()
        assert "Target date must be in the future" in str(serializer.errors['target_date'])

    def test_goal_is_active_logic(self, test_user):
        """Test the is_active property logic."""
        future_date = timezone.now().date() + timedelta(days=30)

        goal = SavingsGoal.objects.create(
            user=test_user,
            name="Active Test",
            target_amount=Decimal('1000.00'),
            regular_contribution=Decimal('100.00'),
            target_date=future_date,
            frequency="monthly"
        )

        # Initially Active
        assert goal.is_active is True

        # Complete the goal
        GoalContribution.objects.create(goal=goal, amount=Decimal('1000.00'), is_verified=True)

        # Should now be inactive (completed)
        assert goal.is_active is False

    def test_goal_expiry_logic(self, test_user):
        """Test that past target_date makes goal inactive."""
        past_date = timezone.now().date() - timedelta(days=1)

        # Bypass validation by creating directly in DB
        goal = SavingsGoal.objects.create(
            user=test_user,
            name="Expired Test",
            target_amount=Decimal('1000.00'),
            regular_contribution=Decimal('100.00'),
            target_date=past_date,
            frequency="monthly"
        )

        assert goal.is_active is False


@pytest.mark.django_db
def test_goal_delete_cascades_contributions(test_user):
    """
    Verify delete removes related contributions via CASCADE.
    """
    future_date = date(2026, 12, 31)  # Safe future date (current date: Jan 07, 2026)

    goal = SavingsGoal.objects.create(
        user=test_user,
        name="Delete Test",
        target_amount=Decimal('1000.00'),
        regular_contribution=Decimal('100.00'),
        target_date=future_date,
        frequency="monthly"
    )
    GoalContribution.objects.create(goal=goal, amount=Decimal('50.00'), is_verified=True)

    assert GoalContribution.objects.filter(goal=goal).exists()

    goal_id = goal.id  # Capture ID before deletion
    goal.delete()

    # Filter by ID instead of instance (deleted instance has pk=None)
    assert not GoalContribution.objects.filter(goal_id=goal_id).exists()


@pytest.mark.django_db
def test_goal_update_target_validation(test_user):
    """
    Can't reduce target below current_saved.
    """
    future_date = date(2026, 12, 31)

    goal = SavingsGoal.objects.create(
        user=test_user,
        name="Update Test",
        target_amount=Decimal('1000.00'),
        regular_contribution=Decimal('100.00'),
        target_date=future_date,
        frequency="monthly"
    )
    GoalContribution.objects.create(goal=goal, amount=Decimal('500.00'), is_verified=True)

    # Attempt invalid update
    from accounts.serializers import SavingsGoalUpdateSerializer
    serializer = SavingsGoalUpdateSerializer(instance=goal, data={"target_amount": Decimal('400.00')}, partial=True)
    assert not serializer.is_valid()
    assert "Cannot set target below current saved" in str(serializer.errors)

@pytest.mark.django_db
def test_goal_over_contribution_prevention(auth_client, test_user, funded_wallet):
    """
    Test that users cannot contribute more than the remaining target amount.
    """
    # Setup: Create a goal close to completion
    goal = SavingsGoal.objects.create(
        user=test_user,
        name="Laptop",
        target_amount=1000.00,
        regular_contribution=500.00,
        target_date="2026-12-31",
        frequency="monthly"
    )

    # Simulate existing contributions (900 paid, 100 remaining)
    GoalContribution.objects.create(goal=goal, amount=900.00, is_verified=True)

    url = reverse('goal-contribute', kwargs={'goal_id': goal.id})
    headers = {'HTTP_X_IDEMPOTENCY_KEY': 'test-key-123'}
    response = auth_client.post(url, **headers)

    # Verify Rejection
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "exceeds target" in str(response.data['error'])
    assert response.data['remaining_needed'] == 100.00

    # Verify no money was taken
    funded_wallet.refresh_from_db()
    assert funded_wallet.current_balance == 500.00
