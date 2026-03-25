import pytest
import requests
from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from celery.exceptions import Retry
from django.core.cache import cache
from django.db import IntegrityError
from accounts.services import LedgerService
from django.contrib.auth import get_user_model
from accounts.models import SavingsGroup, IdempotencyKey, GroupAdminKYC, LedgerEntry, GroupMembership, Profile
from accounts.tasks import clear_old_idempotency_keys, send_dawurobo_otp_async

User = get_user_model()

@pytest.mark.django_db
class TestLedgerReliability:
    def test_transfer_atomic_rollback_on_credit_failure(self, test_user, test_group, funded_wallet):
        """
        CRITICAL: Verify that if the Credit (Destination) leg fails,
        the Debit (Source) leg is rolled back. Money must not disappear.
        """
        initial_balance = funded_wallet.current_balance
        transfer_amount = Decimal("50.00")

        original_create = LedgerService.create_entry

        def side_effect(*args, **kwargs):
            if kwargs.get('direction') == 'credit':
                raise IntegrityError("Simulated DB Crash during Credit")
            return original_create(*args, **kwargs)

        with patch.object(LedgerService, 'create_entry', side_effect=side_effect):
            with pytest.raises(IntegrityError):
                LedgerService.transfer(
                    amount=transfer_amount,
                    transaction_type='contribution',
                    description="Rollback Test",
                    reference="ROLLBACK-001",
                    from_user=test_user,
                    to_group=test_group
                )

        funded_wallet.refresh_from_db()
        assert funded_wallet.current_balance == initial_balance
        assert LedgerEntry.objects.filter(reference__contains="ROLLBACK-001").count() == 0

@pytest.mark.django_db
class TestPayoutMath:
    def test_payout_rotation_modulo_logic(self, test_group, membership):
        test_group.expected_members = 3
        test_group.save()

        def get_position(cycle, members):
            return ((cycle - 1) % members) + 1

        assert get_position(1, 3) == 1
        assert get_position(2, 3) == 2
        assert get_position(3, 3) == 3
        assert get_position(4, 3) == 1
        assert get_position(6, 3) == 3

@pytest.mark.django_db
class TestGoalFinancials:
    def test_goal_contribution_moves_money(self, auth_client, test_user, funded_wallet):
        """
        Verify successful goal contributions actually move money via the Ledger.
        """
        from accounts.models import SavingsGoal

        goal = SavingsGoal.objects.create(
            user=test_user,
            name="Test Goal",
            target_amount=1000,
            regular_contribution=100,
            target_date="2026-01-01",
            frequency="monthly"
        )

        url = reverse('goal-contribute', kwargs={'goal_id': goal.id})
        response = auth_client.post(url, {}, HTTP_X_IDEMPOTENCY_KEY="goal-money-test")

        assert response.status_code == 201

        funded_wallet.refresh_from_db()
        # Started at 500, minus 100 should be 400.
        assert funded_wallet.current_balance == Decimal("400.00")

        entry = LedgerEntry.objects.filter(
            transaction_type='goal_contribution',
            wallet=funded_wallet,
            direction='debit'
        ).first()

        assert entry is not None
        assert entry.amount == Decimal("100.00")

@pytest.mark.django_db
class TestDataIntegrity:

    def test_phone_number_normalization_hashing(self):
        """
        Ensure that different input formats for the same number
        result in the same hash.
        Uses a FRESH user to avoid collision with the fixture.
        """
        fresh_user = User.objects.create_user(
            username="phone_test_user",
            email="phone_test@example.com",
            password="pass"
        )

        local_format = "054 412 3456"

        profile = Profile.objects.create(
            user=fresh_user,
            full_name="Phone Test",
            date_of_birth="1990-01-01",
            user_type="individual",
            ghana_post_address="GA-000-0000",
            momo_provider="mtn",
            momo_number=local_format,
            momo_name="Test"
        )

        original_hash = profile.momo_number_hash
        assert profile.momo_number == "+233544123456"

        import phonenumbers
        import hashlib
        from django.conf import settings

        input_international = "+233 54 412 3456"

        parsed = phonenumbers.parse(input_international, "GH")
        normalized = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        salt = getattr(settings, "HASH_SALT", settings.SECRET_KEY)
        hash_input = f"{normalized}{salt}".encode('utf-8')
        calculated_hash = hashlib.sha256(hash_input).hexdigest()

        assert calculated_hash == original_hash

        # Verify Lookup matches
        found_profile = Profile.objects.get(momo_number_hash=calculated_hash)
        assert found_profile == profile

@pytest.mark.django_db
class TestSecurityRateLimiting:
    """
    Validates that the @ratelimit decorators actually block brute-force attacks.
    """

    def test_otp_verification_rate_limit(self, api_client, test_user):
        """
        The VerifyOTPView allows 5 requests per minute.
        We verify that eventually, within a short burst, the API blocks us.
        """
        cache.clear()

        url = reverse('otp-verify')
        data = {"phone_number": "0244123456", "code": "123456"}

        with patch("accounts.views.verify_and_invalidate_otp_sync", return_value=False):

            blocked = False
            for i in range(10):
                response = api_client.post(url, data)

                if response.status_code in [status.HTTP_429_TOO_MANY_REQUESTS, status.HTTP_403_FORBIDDEN]:
                    blocked = True
                    break

            assert blocked, "Rate limit was not triggered after 10 attempts (Expected limit: 5/m)"

@pytest.mark.django_db
class TestSearchAndFiltering:
    """
    Validates the 'AllGroupsListView' filters to ensure users can discover groups.
    """

    def test_filter_groups_by_frequency_and_search(self, auth_client, test_user):
        group_a = SavingsGroup.objects.create(
            admin=test_user, name="Bali Trip", group_name="bali-2026",
            contribution_amount=100, frequency='weekly', expected_members=10,
            status='active', description="Saving for a vacation", payout_timeline_days=30
        )
        GroupMembership.objects.create(user=test_user, group=group_a)

        SavingsGroup.objects.create(
            admin=test_user, name="New Car", group_name="benz-2026",
            contribution_amount=500, frequency='monthly', expected_members=5,
            status='active', description="Buying a vehicle", payout_timeline_days=30
        )
        SavingsGroup.objects.create(
            admin=test_user, name="Xmas", group_name="xmas-2026",
            contribution_amount=50, frequency='weekly', expected_members=10,
            status='active', description="Gifts", payout_timeline_days=30
        )

        url = reverse('all-groups')

        resp = auth_client.get(url, {'frequency': 'weekly'})
        assert resp.status_code == 200
        assert len(resp.data['results']) == 2

        resp = auth_client.get(url, {'search': 'vehicle'})
        assert len(resp.data['results']) == 1
        assert resp.data['results'][0]['group_name'] == 'benz-2026'

        resp = auth_client.get(url, {'frequency': 'weekly', 'search': 'vacation'})
        assert len(resp.data['results']) == 1
        assert resp.data['results'][0]['group_name'] == 'bali-2026'

@pytest.mark.django_db
class TestOperationalResilience:
    """
    Tests infrastructure reliability (Celery Retries, DB Cleanup).
    """

    def test_otp_task_retries_on_network_failure(self):
        """
        If the SMS provider raises a RequestException, the task should Retry.
        """
        # Mock the task's retry method
        with patch('accounts.tasks.send_dawurobo_otp_async.retry') as mock_retry:

            mock_retry.side_effect = Retry()

            with patch('requests.post', side_effect=requests.exceptions.ConnectionError("DNS Fail")):

                try:
                    send_dawurobo_otp_async(phone_number="0244000000")
                except Retry:
                    pass
                except requests.exceptions.ConnectionError:
                    pass

                assert mock_retry.called
                call_args = mock_retry.call_args
                assert isinstance(call_args.kwargs['exc'], requests.exceptions.ConnectionError)

    def test_idempotency_key_cleanup_task(self, test_user):
        """
        Ensure the maintenance task deletes old keys but keeps fresh ones.
        """
        now = timezone.now()

        fresh = IdempotencyKey.objects.create(
            user=test_user, key="fresh-key", request_hash="abc",
            response_code=200, response_body={}
        )

        old = IdempotencyKey.objects.create(
            user=test_user, key="old-key", request_hash="xyz",
            response_code=200, response_body={}
        )
        IdempotencyKey.objects.filter(pk=old.pk).update(created_at=now - timedelta(hours=50))

        result_msg = clear_old_idempotency_keys()

        assert "Purged 1" in result_msg
        assert IdempotencyKey.objects.filter(pk=fresh.pk).exists()
        assert not IdempotencyKey.objects.filter(pk=old.pk).exists()


@pytest.mark.django_db
class TestAdminWorkflows:

    def test_kyc_approval_side_effects(self, test_user):
        """
        Integration: When a KYC record is approved, the User.is_verified flag
        must implicitly flip to True.
        """
        test_user.is_verified = False
        test_user.save()

        kyc = GroupAdminKYC.objects.create(user=test_user)
        assert kyc.is_manually_verified is False

        # Action: Admin approves
        admin = test_user
        kyc.approve(admin_user=admin)

        # Verification
        kyc.refresh_from_db()
        test_user.refresh_from_db()

        assert kyc.is_manually_verified is True
        assert test_user.is_verified is True


@pytest.mark.django_db
class TestLedgerCapabilities:

    def test_service_can_process_refunds(self, test_user, funded_wallet):
        """
        Ensure LedgerService can handle 'refund' transactions correctly.
        """
        assert funded_wallet.current_balance == Decimal("500.00")

        refund_amount = Decimal("50.00")

        LedgerService.create_entry(
            user=test_user,
            amount=refund_amount,
            direction='credit',
            transaction_type='refund',
            description="Refund for failed transaction",
            reference="REFUND-001",
            actor=test_user
        )

        funded_wallet.refresh_from_db()
        assert funded_wallet.current_balance == Decimal("550.00")

        entry = LedgerEntry.objects.get(reference="REFUND-001")
        assert entry.transaction_type == 'refund'
        assert entry.direction == 'credit'
