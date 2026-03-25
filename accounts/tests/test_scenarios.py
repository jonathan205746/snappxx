import pytest
from decimal import Decimal
from django.urls import reverse
from accounts.models import SavingsGroup, Contribution, GroupMembership, LedgerEntry, PayoutOrder
from accounts.tasks import execute_group_payout
from rest_framework.test import APIClient

@pytest.mark.django_db
class TestEdgeCases:

    def test_contribution_blocked_if_group_stale_or_pending(self, test_user, funded_wallet):
        """
        Verify that users cannot contribute to a group that hasn't filled up
        (status='pending'). This ensures money never gets trapped in 'stale' groups.
        """
        stale_group = SavingsGroup.objects.create(
            admin=test_user,
            group_name="stale-group-001",
            contribution_amount=Decimal("100.00"),
            frequency='monthly',
            expected_members=5,
            status='pending',
            payout_timeline_days=30
        )

        GroupMembership.objects.create(user=test_user, group=stale_group)

        client = APIClient()
        client.force_authenticate(user=test_user)
        url = reverse('group-contribute', kwargs={'group_id': stale_group.id})

        response = client.post(
            url,
            {},
            HTTP_X_IDEMPOTENCY_KEY="stale-check-001"
        )

        assert response.status_code == 404
        assert "Group not found or not active" in str(response.data)

        funded_wallet.refresh_from_db()
        assert funded_wallet.current_balance == Decimal("500.00")  # Balance remains untouched


    def test_payout_deadlock_insufficient_funds(self, test_group, test_user, funded_wallet, membership):
        """
        Scenario: Group expects 2 members. Only 1 contributes. Payout triggers.
        Expectation: Payout aborts safely.
        Risk: Code logs error but doesn't schedule a retry (Deadlock).
        """
        test_group.status = 'active'
        test_group.expected_members = 2
        test_group.contribution_amount = Decimal("100.00")
        test_group.current_cycle_number = 1
        test_group.save()

        PayoutOrder.objects.create(group=test_group, membership=membership, position=1)

        Contribution.objects.create(
            membership=membership,
            amount=Decimal("100.00"),
            cycle_number=1,
            is_verified=True
        )

        test_group.group_wallet.current_balance = Decimal("100.00")
        test_group.group_wallet.save()

        execute_group_payout(test_group.id)

        test_group.refresh_from_db()
        test_group.group_wallet.refresh_from_db()
        funded_wallet.refresh_from_db()

        # Payout should NOT have happened
        assert test_group.current_cycle_number == 1
        assert test_group.group_wallet.current_balance == Decimal("100.00")

        payout_credits = LedgerEntry.objects.filter(
            wallet=funded_wallet,
            transaction_type='payout'
        ).count()
        assert payout_credits == 0


    def test_member_removal_refund_gap(self, test_group, test_user, funded_wallet, membership):
        """
        Demonstrates that even if we force-delete a member, the money is NOT returned.
        This proves the need for a dedicated `MemberService.remove_member()` method.
        """
        # Setup: User contributes 100 GHS
        amount = Decimal("100.00")

        # Debit User
        funded_wallet.current_balance -= amount
        funded_wallet.save()

        # Credit Group
        test_group.group_wallet.current_balance += amount
        test_group.group_wallet.save()

        # Record Contribution
        Contribution.objects.create(
            membership=membership,
            amount=amount,
            cycle_number=1,
            is_verified=True
        )

        Contribution.objects.filter(membership=membership).delete()

        membership.delete()
        funded_wallet.refresh_from_db()
        test_group.group_wallet.refresh_from_db()

        assert funded_wallet.current_balance == Decimal("400.00")

        assert test_group.group_wallet.current_balance == Decimal("100.00")

        refund_exists = LedgerEntry.objects.filter(
            wallet=funded_wallet,
            transaction_type='refund'
        ).exists()

        assert not refund_exists
