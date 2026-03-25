import jwt
import pytest
from decimal import Decimal
from datetime import datetime
from django.urls import reverse
from django.utils import timezone
from django.http import HttpResponse
from django.test import RequestFactory
from accounts.models import GroupAdminKYC
from django.db.models import ProtectedError
from rest_framework.response import Response
from accounts.utils import idempotent_request
from django.contrib.auth import get_user_model
from core.middleware import RequestIDMiddleware
from accounts.serializers import ProfileSerializer, SavingsGroupCreateSerializer

User = get_user_model()

@pytest.mark.django_db
class TestJWTRememberMe:
    """
    Verifies the variable Time-To-Live (TTL) logic in the Login Serializer.
    Ref: accounts/serializers.py
    """

    def test_remember_me_extends_token_lifetime(self, api_client, test_user):
        url = reverse('login')
        password = "password123"
        test_user.set_password(password)
        test_user.save()

        # Standard Login (Short Lived)
        resp_standard = api_client.post(url, {
            "login_field": test_user.email,
            "password": password,
            "remember_me": False
        })
        assert resp_standard.status_code == 200

        # Decode token to check 'exp' claim (Expiration Time)
        token_std = resp_standard.data['refresh']
        payload_std = jwt.decode(token_std, options={"verify_signature": False})

        # Calculate duration
        exp_std = datetime.fromtimestamp(payload_std['exp'])
        now = datetime.now()
        duration_std = exp_std - now

        # "Remember Me" Login (Long Lived)
        resp_long = api_client.post(url, {
            "login_field": test_user.email,
            "password": password,
            "remember_me": True
        })
        token_long = resp_long.data['refresh']
        payload_long = jwt.decode(token_long, options={"verify_signature": False})

        exp_long = datetime.fromtimestamp(payload_long['exp'])
        duration_long = exp_long - now

        # Assertion: The long token should last significantly longer
        assert duration_long > (duration_std * 20), \
            f"Remember Me did not extend token. Std: {duration_std}, Long: {duration_long}"


class TestMiddlewareInternals:
    """
    Unit testing the RequestIDMiddleware isolation and UUID generation.
    Ref: core/middleware.py
    """

    def test_middleware_generates_uuid_when_missing(self):
        factory = RequestFactory()
        request = factory.get('/')

        # Mock response callback
        def get_response(req):
            return HttpResponse("OK")

        middleware = RequestIDMiddleware(get_response)

        # Execute
        response = middleware(request)

        # Assertions
        assert 'X-Request-ID' in response
        assert len(response['X-Request-ID']) > 10
        # Verify it was attached to the request object
        assert hasattr(request, 'request_id')

    def test_middleware_respects_incoming_header(self):
        factory = RequestFactory()
        trace_id = "google-trace-123"
        request = factory.get('/', HTTP_X_REQUEST_ID=trace_id)

        def get_response(req):
            return HttpResponse("OK")

        middleware = RequestIDMiddleware(get_response)
        response = middleware(request)

        assert response['X-Request-ID'] == trace_id
        assert request.request_id == trace_id


@pytest.mark.django_db
class TestIdempotencyComplexSerialization:
    """
    Ensures the Idempotency layer doesn't crash when Views return
    non-JSON-native types like Decimal or Date.
    Ref: accounts/utils.py
    """

    def test_idempotency_handles_decimal_response(self, test_user):
        # Create a dummy view specifically returning Decimals
        class MockView:
            @idempotent_request
            def post(self, request):
                return Response({
                    "balance": Decimal("100.50"),
                    "date": datetime.now().date()
                }, status=201)

        # Mock Request
        factory = RequestFactory()
        data_payload = {"a": 1}
        request = factory.post('/dummy/', data=data_payload, content_type='application/json')

        # Manually attach .data to mimic DRF behavior
        request.data = data_payload
        request.headers = {'X-Idempotency-Key': 'decimal-test-key-unique-3'}
        request.user = test_user

        # Execute View
        view_instance = MockView()

        try:
            response = view_instance.post(request)
        except Exception as e:
            pytest.fail(f"Idempotency middleware crashed: {e}")

        # Verify Response
        assert response.status_code == 201
        assert response.data['balance'] == Decimal("100.50")



@pytest.mark.django_db
class TestInputSanitization:
    """
    Verifies that 'Never Trust Client' sanitization logic works.
    Ref: accounts/serializers.py
    """

    def test_profile_fullname_escapes_html(self):
        """ProfileSerializer should escape HTML in full_name."""
        # Create a FRESH user without a profile to avoid IntegrityError
        clean_user = User.objects.create_user(
            username="clean_input_user",
            email="clean@input.com",
            password="pass"
        )

        xss_payload = "<script>alert('XSS')</script>"

        # Mock request context
        class MockReq:
            user = clean_user
            method = 'POST'

        serializer = ProfileSerializer(data={
            "full_name": xss_payload,
            "date_of_birth": "1990-01-01",
            "user_type": "worker",
            "ghana_post_address": "GA-123-4567",
            "momo_provider": "mtn",
            "momo_number": "0540000000",
            "momo_name": "Test"
        }, context={'request': MockReq()})

        assert serializer.is_valid(), serializer.errors
        saved_profile = serializer.save(user=clean_user)

        # Assert the DB contains the safe version
        assert "&lt;script&gt;" in saved_profile.full_name
        assert "<script>" not in saved_profile.full_name

    def test_group_name_sanitization(self, test_user):
        """SavingsGroupCreateSerializer should sanitize group names."""
        bad_name = "<b>Bold Group</b>"

        # Pre-create KYC so the serializer
        GroupAdminKYC.objects.create(
            user=test_user,
            is_manually_verified=True,
            verified_at=timezone.now()
        )

        class MockReq:
            user = test_user
            method = 'POST'

        serializer = SavingsGroupCreateSerializer(data={
            "group_name": bad_name,
            "contribution_amount": 100,
            "frequency": "daily",
            "payout_timeline_days": 1,
            "expected_members": 2,
            "description": "desc"
        }, context={'request': MockReq()})

        assert serializer.is_valid(), serializer.errors
        assert "&lt;b&gt;" in serializer.validated_data['group_name']


@pytest.mark.django_db
class TestDatabaseConstraints:
    """
    Verifies referential integrity settings (on_delete behavior).
    Ref: accounts/models.py
    """

    def test_cannot_delete_admin_of_active_group(self, test_user, test_group):
        """
        Critical Integrity Check: If a user is the Admin of a group,
        deleting the User object must be blocked by the DB.
        """
        # Ensure relation exists
        assert test_group.admin == test_user

        # Attempt to delete the user
        with pytest.raises(ProtectedError):
            test_user.delete()

        # Verify user still exists
        test_user.refresh_from_db()
        assert test_user is not None
