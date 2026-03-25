# accounts/views.py

import uuid
import hashlib
import logging
import cloudinary.uploader
from django.conf import settings
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample, inline_serializer
from .models import LedgerEntry, Wallet, SavingsGroup, Profile, GroupJoinRequest, GroupMembership, Contribution, PayoutOrder,  SavingsGoal, GoalContribution
from .tasks import clear_old_idempotency_keys, process_daily_payouts, reconcile_financial_integrity, send_dawurobo_otp_async, send_goal_reminders, verify_and_invalidate_otp_sync, send_group_join_request_email_async, send_group_join_response_email_async
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.views import TokenObtainPairView
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import serializers as rest_serializers
from django.views.decorators.cache import never_cache
from django.utils.decorators import method_decorator
from rest_framework.parsers import MultiPartParser, JSONParser
from django.db import transaction, IntegrityError
from django_ratelimit.decorators import ratelimit
from rest_framework.filters import SearchFilter
from django.contrib.auth import get_user_model
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import generics, status, permissions
from .permissions import IsGroupAdmin, IsGoalOwner
from django.utils import timezone
from django.db.models import Sum, F, Q
from dateutil.relativedelta import relativedelta
import phonenumbers
from decimal import Decimal
from rest_framework.generics import RetrieveUpdateDestroyAPIView
from rest_framework import serializers, status
from .utils import idempotent_request
from .services import LedgerService
from .serializers import (
    AnalyticsResponseSerializer, ChangePasswordSerializer, GoalDashboardCardSerializer, SavingsGroupCreateSerializer, SavingsGroupSerializer, SendOTPSerializer, VerifyOTPSerializer,
    CustomTokenObtainPairSerializer, ForgotPasswordSerializer, ResetPasswordSerializer, ProfileSerializer,
    FullSignupSerializer, GroupJoinRequestSerializer, GroupJoinActionSerializer, GroupDashboardCardSerializer,
    DashboardResponseSerializer, SavingsGoalCreateSerializer, GoalsDashboardResponseSerializer,
    SavingsGoalSerializer, SavingsGoalUpdateSerializer, GroupsStatsResponseSerializer,
    JoinRequestsStatsSerializer, GroupJoinRequestCreateSerializer
)

from django.db.models.functions import TruncMonth
from django_qstash import publish
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import IsAdminUser
from django_qstash import stashed_task
from django.conf import settings

logger = logging.getLogger('accounts.finance')
User = get_user_model()

class CustomLoginView(TokenObtainPairView):
    permission_classes = [AllowAny]
    serializer_class = CustomTokenObtainPairSerializer


class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]
    serializer_class = ForgotPasswordSerializer

    @method_decorator(ratelimit(key='ip', rate='10/m', method='POST', block=True))
    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        User = get_user_model()
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        login_field = serializer.validated_data['login_field'].lower().strip()

        try:
            if '@' in login_field:
                user = User.objects.select_related('profile').get(email=login_field)
            else:
                user = User.objects.select_related('profile').get(profile__momo_number=login_field)
        except User.DoesNotExist:
            return Response({"error": "User not found with this email or phone"},
                            status=status.HTTP_404_NOT_FOUND)

        momo_number = str(user.profile.momo_number)
        result = send_dawurobo_otp_async(momo_number)

        if result.get("success"):
            return Response({
                "message": "OTP sent to your registered phone for password reset.",
                "phone": momo_number
            }, status=status.HTTP_200_OK)
        else:
            return Response({"error": "Failed to send OTP. Try again later."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@method_decorator([
    never_cache,
    ratelimit(key='ip', rate='5/h', method='POST', block=True)
], name='dispatch')
@extend_schema(
    request=FullSignupSerializer,
    responses={
        201: {
            'type': 'object',
            'properties': {
                'message': {'type': 'string'},
                'phone': {'type': 'string'},
                'next_step': {'type': 'string'},
            }
        },
        400: {'description': 'Validation or uniqueness error'},
        500: {'description': 'Server or OTP sending error'},
    },
    description="Complete user and profile registration, including optional file upload. Triggers phone verification.",
    tags=['Authentication & Registration']
)
class FullSignupView(APIView):
    permission_classes = [permissions.AllowAny]
    parser_classes = [MultiPartParser]

    @extend_schema(description="Register user and profile. Triggers OTP.")
    @transaction.atomic
    @idempotent_request
    def post(self, request):
        data = request.data

        required_fields = [
            'email', 'password', 'password2', 'full_name', 'date_of_birth',
            'user_type', 'ghana_post_address', 'momo_provider', 'momo_number', 'momo_name'
        ]
        for field in required_fields:
            if not data.get(field):
                return Response(
                    {"error": f"{field.replace('_', ' ').title()} is required"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        if data['password'] != data['password2']:
            return Response({"error": "Passwords do not match"}, status=status.HTTP_400_BAD_REQUEST)

        email = data['email'].lower().strip()
        momo_number = str(data['momo_number']).strip()

        if User.objects.filter(email=email).exists():
            return Response({"error": "This email is already registered"}, status=status.HTTP_400_BAD_REQUEST)
        if Profile.objects.filter(momo_number=momo_number).exists():
            return Response({"error": "This MoMo number is already registered"}, status=status.HTTP_400_BAD_REQUEST)

        # Handle File Upload
        profile_picture_url = None
        if 'profile_picture' in request.FILES:
            try:
                upload_result = cloudinary.uploader.upload(
                    request.FILES['profile_picture'],
                    folder="snappx/profiles/",
                    transformation=[
                        {'width': 500, 'height': 500, 'crop': 'limit'},
                        {'quality': "auto"}
                    ]
                )
                profile_picture_url = upload_result.get('secure_url')
            except Exception as e:
                logger.warning(f"Cloudinary upload failed: {e}")

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    email=email,
                    username=email.split('@')[0],
                    password=data['password'],
                    is_verified=False
                )

                # Create Profile
                Profile.objects.create(
                    user=user,
                    full_name=data['full_name'],
                    date_of_birth=data['date_of_birth'],
                    user_type=data['user_type'],
                    ghana_post_address=data['ghana_post_address'],
                    profile_picture=profile_picture_url,
                    momo_provider=data['momo_provider'],
                    momo_number=momo_number,
                    momo_name=data['momo_name']
                )

                otp_result = send_dawurobo_otp_async(momo_number)
                if not otp_result.get("success"):
                    raise Exception("SMS provider error")

        except Exception as e:
            logger.error(f"Signup Transaction Failed: {str(e)}")
            return Response({"error": "Registration failed. Please try again later."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        logger.info({
            "event": "user_registered",
            "user_id": user.id,
            "momo_mask": f"***-{momo_number[-4:]}",
            "wallet_initialized": True
        })

        return Response({
            "message": "Account created successfully! OTP sent.",
            "phone": momo_number,
            "next_step": "verify_otp"
        }, status=status.HTTP_201_CREATED)


@method_decorator([never_cache, ratelimit(key='ip', rate='5/m', method='POST', block=True)], name='dispatch')
class ResetPasswordView(APIView):
    permission_classes = [AllowAny]
    serializer_class = ResetPasswordSerializer

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        phone = serializer.validated_data['phone']
        code = serializer.validated_data['code']
        new_password = serializer.validated_data['password']

        if not verify_and_invalidate_otp_sync(phone, code):
            return Response({"error": "Invalid or expired OTP"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Normalize the phone number
            try:
                parsed_num = phonenumbers.parse(phone, "GH")
                if phonenumbers.is_valid_number(parsed_num):
                    normalized_phone = phonenumbers.format_number(parsed_num, phonenumbers.PhoneNumberFormat.E164)
                else:
                    normalized_phone = phone.strip()
            except Exception:
                normalized_phone = phone.strip()

            # Generate the Hash
            salt = getattr(settings, "HASH_SALT", settings.SECRET_KEY)
            hash_input = f"{normalized_phone}{salt}".encode('utf-8')
            phone_hash = hashlib.sha256(hash_input).hexdigest()

            # Lookup using the HASH field, NOT the encrypted field
            profile = Profile.objects.select_related('user').get(momo_number_hash=phone_hash)

            user = profile.user
            user.set_password(new_password)
            user.save(update_fields=['password'])

            logger.info(f"Password reset successful for {user.email}")
            return Response({"message": "Password reset successful. You can now log in."})

        except Profile.DoesNotExist:
            return Response({"error": "Invalid request"}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    request=ChangePasswordSerializer,
    responses={
        200: inline_serializer(
            name="PasswordChangeSuccess",
            fields={"message": serializers.CharField()}
        ),
        400: {"description": "Current password incorrect or validation error"},
    },
    description="Change password while logged in. Requires current password for security.",
    tags=["User Management"]
)
class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    @method_decorator([
        never_cache,
        ratelimit(key='user', rate='3/h', method='POST', block=True)
    ], name='dispatch')
    @idempotent_request
    @transaction.atomic
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        current_password = serializer.validated_data['current_password']
        new_password = serializer.validated_data['new_password']

        if not request.user.check_password(current_password):
            return Response(
                {"error": "Current password is incorrect"},
                status=status.HTTP_400_BAD_REQUEST
            )

        request.user.set_password(new_password)
        request.user.save(update_fields=['password'])

        logger.info({
            "event": "password_changed",
            "user_id": request.user.id,
            "request_id": getattr(request, 'request_id', None)
        })

        return Response({
            "message": "Password updated successfully. Please log in again with the new password."
        }, status=status.HTTP_200_OK)


class SendOTPView(APIView):
    permission_classes = [AllowAny]
    serializer_class = SendOTPSerializer

    @method_decorator(ratelimit(key='ip', rate='10/m', method='POST', block=True))
    def post(self, request):
        serializer = SendOTPSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        phone = serializer.validated_data['phone_number']
        result = send_dawurobo_otp_async(phone)

        if result.get("success"):
            return Response({"message": "OTP sent again!"}, status=200)
        else:
            return Response({"error": "Failed to send OTP"}, status=500)

@method_decorator([
    never_cache,
    ratelimit(key='ip', rate='5/m', method='POST', block=True),
    ratelimit(key='post:phone_number', rate='3/m', method='POST', block=True)
], name='dispatch')
class VerifyOTPView(APIView):
    permission_classes = [AllowAny]
    serializer_class = VerifyOTPSerializer

    def post(self, request):
        serializer = VerifyOTPSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        phone = serializer.validated_data['phone_number']
        code = serializer.validated_data['code']

        try:
            parsed_num = phonenumbers.parse(phone, "GH")
            if phonenumbers.is_valid_number(parsed_num):
                normalized_phone = phonenumbers.format_number(parsed_num, phonenumbers.PhoneNumberFormat.E164)
            else:
                normalized_phone = phone.strip()
        except Exception:
            normalized_phone = phone.strip()

        if verify_and_invalidate_otp_sync(phone, code):
            try:
                salt = getattr(settings, "HASH_SALT", settings.SECRET_KEY)
                hash_input = f"{normalized_phone}{salt}".encode('utf-8')
                phone_hash = hashlib.sha256(hash_input).hexdigest()

                profile = Profile.objects.get(momo_number_hash=phone_hash)
                profile.user.is_verified = True
                profile.user.save(update_fields=['is_verified'])

                return Response({
                    "success": True,
                    "message": "Welcome to SnappX! Your account is verified."
                }, status=200)
            except Profile.DoesNotExist:
                return Response({"error": "Account not found"}, status=404)
        else:
            return Response({"error": "Invalid or expired OTP"}, status=400)


class MeViewResponseSerializer(rest_serializers.Serializer):
    user = rest_serializers.DictField(
        child=rest_serializers.CharField(),
        help_text="Basic user fields like ID, email, and verification status."
    )
    profile = ProfileSerializer()


class MeView(APIView):
    permission_classes = [IsAuthenticated]
    @extend_schema(
        responses={200: MeViewResponseSerializer},
        description="Retrieves the current authenticated user's details and profile data.",
        tags=['User Management']
    )
    def get(self, request):
        User = get_user_model()
        user = User.objects.select_related('profile').get(pk=request.user.pk)
        profile = user.profile
        return Response({
            "user": {
                "id": user.id,
                "email": user.email,
                "is_verified": user.is_verified,
                "date_joined": user.date_joined.isoformat()
            },
            "profile": ProfileSerializer(profile).data
        })


class CreateSavingsGroupView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    @transaction.atomic
    def post(self, request):
        serializer = SavingsGroupCreateSerializer(data=request.data, context={'request': request})

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            group = serializer.save(admin=request.user)

            from .models import GroupMembership, PayoutOrder
            membership = GroupMembership.objects.get(user=request.user, group=group)

            PayoutOrder.objects.get_or_create(
                group=group,
                membership=membership,
                defaults={'position': 1}
            )

            logger.info(f"Group {group.group_name} created by {request.user.email}.")

            return Response({
                "success": True,
                "message": "Savings group created!",
                "group_id": group.id
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Group creation failed: {e}")
            return Response({"error": "Failed to create group."}, status=500)

@extend_schema(
    description="Lists all savings groups the authenticated user is a member of (including groups they created/admin). "
                "Includes personal total_savings and next_due date.",
    tags=['Savings Groups'],
    responses={
        200: SavingsGroupSerializer(many=True),
        401: {'description': 'Authentication credentials were not provided.'}
    }
)
class MyJoinedGroupsListView(generics.ListAPIView):
    """
    Shows groups the user has joined (as member or admin).
    """
    serializer_class = SavingsGroupSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        return SavingsGroup.objects.filter(
            memberships__user=user
        ).select_related('admin__profile').distinct()


@extend_schema(
    parameters=[
        OpenApiParameter(
            name='public_id',
            type=str,
            location=OpenApiParameter.PATH,
            description='The public_id of the savings group (or legacy numeric id for backward compatibility).',
            required=True
        ),
    ],
    description="Retrieves the details of a single savings group. "
                "Accessible to any member of the group (not just the admin). "
                "Includes member's personal total_savings and next_due date.",
    tags=['Savings Groups'],
    responses={
        200: SavingsGroupSerializer,
        401: {'description': 'Authentication required.'},
        403: {'description': 'You are not a member of this group.'},
        404: {'description': 'Group not found.'}
    }
)
class GroupDetailView(generics.RetrieveAPIView):
    """
    Supports BOTH:
    - New groups: long random public_id
    - Legacy groups (public_id is still NULL): falls back to numeric id
    """
    serializer_class = SavingsGroupSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'public_id'

    def get_queryset(self):
        user = self.request.user
        return SavingsGroup.objects.filter(
            memberships__user=user
        ).select_related('admin__profile')

    def get_object(self):
        queryset = self.get_queryset()
        lookup_value = self.kwargs['public_id']

        # 1. Preferred: new public_id (long random string)
        obj = queryset.filter(public_id=lookup_value).first()
        if obj is not None:
            self.check_object_permissions(self.request, obj)
            return obj

        # 2. Legacy fallback: numeric ID (for groups created before public_id existed)
        if str(lookup_value).isdigit():
            obj = queryset.filter(id=int(lookup_value)).first()
            if obj is not None:
                self.check_object_permissions(self.request, obj)
                return obj

        # Same 404 message you already see – no behaviour change
        from rest_framework.exceptions import NotFound
        raise NotFound({"detail": "No SavingsGroup matches the given query."})

@extend_schema(
    description="Lists all Active savings groups across the platform, allowing filtering and searching.",
    tags=['Savings Groups'],
    parameters=[
        OpenApiParameter(
            name='search',
            type={'type': 'string'},
            location=OpenApiParameter.QUERY,
            description='Search by group name or description.'
        ),
        OpenApiParameter(
            name='frequency',
            type={'type': 'string'},
            location=OpenApiParameter.QUERY,
            description='Filter by contribution frequency (e.g., daily, weekly, monthly).'
        ),
        OpenApiParameter(
            name='expected_members',
            type={'type': 'integer'},
            location=OpenApiParameter.QUERY,
            description='Filter by exact expected number of members.'
        ),
    ],
    examples=[
        OpenApiExample(
            name='Filter and Search Example',
            description='Retrieve all weekly groups with "vacation" in the name/description.',
            value={
                'search': 'vacation',
                'frequency': 'weekly'
            },
            request_only=True
        ),
    ],
    responses={
        200: SavingsGroupSerializer(many=True),
        401: {'description': 'Authentication credentials were not provided.'}
    }
)
class AllGroupsListView(generics.ListAPIView):
    """Lists all active savings groups for the platform, with filtering and searching."""
    serializer_class = SavingsGroupSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter]

    filterset_fields = ['frequency', 'expected_members', 'contribution_amount']

    search_fields = ['group_name', 'description']

    def get_queryset(self):
        # Only show groups that have been approved by an admin
        return (
            SavingsGroup.objects
            .filter(status='active')
            .select_related('admin__profile')
        )


class GroupJoinRequestView(APIView):
    """Endpoint for users to request to join a group."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
            request=GroupJoinRequestCreateSerializer,
            responses={
                201: inline_serializer(
                    name='JoinRequestSuccess',
                    fields={'message': serializers.CharField()}
                ),
                200: inline_serializer(
                    name='JoinRequestResubmitSuccess',
                    fields={'message': serializers.CharField()}
                ),
                400: {'description': 'Bad request (already member, pending, etc.)'},
                404: {'description': 'Group not found or not active.'}
            },
            description="Submit a request to join an active savings group. "
                        "Optionally include a 'reason' to help the admin decide.",
            tags=['Savings Groups']
        )
    @idempotent_request
    @transaction.atomic
    def post(self, request, group_id):
        serializer = GroupJoinRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get('reason', '')

        try:
            group = SavingsGroup.objects.get(id=group_id, status='active')
        except SavingsGroup.DoesNotExist:
            return Response({"error": "Group not found or not currently active."},
                            status=status.HTTP_404_NOT_FOUND)

        user = request.user

        # Check if already an approved member
        if GroupMembership.objects.filter(user=user, group=group).exists():
            return Response(
                {"error": "You are already a member of this group."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            existing_request = GroupJoinRequest.objects.get(user=user, group=group)
            if existing_request.status == 'approved':
                return Response(
                    {"error": "Your request was already approved."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            elif existing_request.status == 'pending':
                return Response(
                    {"error": "You already have a pending request for this group."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            elif existing_request.status == 'rejected':
                # Allow re-submit with updated reason
                existing_request.status = 'pending'
                existing_request.reason = reason
                existing_request.requested_at = timezone.now()
                existing_request.handled_at = None
                existing_request.handled_by = None
                existing_request.save()

                # Side effect: Trigger email
                send_group_join_request_email_async.delay(existing_request.id)

                return Response({
                    "message": f"Previous request re-submitted to admin of {group.group_name}."
                }, status=status.HTTP_200_OK)

        except GroupJoinRequest.DoesNotExist:
            # Create brand new request
            new_request = GroupJoinRequest.objects.create(
                user=user,
                group=group,
                status='pending',
                reason=reason
            )

            send_group_join_request_email_async.delay(new_request.id)

            return Response({
                "message": f"Join request sent to admin of {group.group_name}. The admin has been notified via email."
            }, status=status.HTTP_201_CREATED)
@extend_schema(
    responses={
        200: GroupJoinRequestSerializer(many=True),
        403: {'description': 'User is not the group admin.'}
    },
    description="Group Admin can view all pending join requests for their specific group.",
    tags=['Savings Groups']
)
class GroupRequestsListView(generics.ListAPIView):
    """Endpoint for Group Admin to list pending join requests."""
    serializer_class = GroupJoinRequestSerializer
    permission_classes = [IsAuthenticated, IsGroupAdmin]

    def get_queryset(self):
        group_id = self.kwargs.get('group_id')
        # Check if the requesting user is the admin of the group
        try:
            group = SavingsGroup.objects.get(id=group_id, admin=self.request.user)
        except SavingsGroup.DoesNotExist:
            raise rest_serializers.ValidationError({"error": "Group not found or you are not the admin."})

        # NEW: Support all statuses via query param (default = pending for backward compatibility)
        status_filter = self.request.query_params.get('status', 'pending')
        if status_filter not in ['pending', 'approved', 'rejected']:
            status_filter = 'pending'

        return GroupJoinRequest.objects.filter(
            group=group,
            status=status_filter
        ).select_related('user__profile', 'group')


@extend_schema(
    request=GroupJoinActionSerializer,
    responses={
        200: {'description': 'Request handled successfully.'},
        400: {'description': 'Invalid action or request already handled.'},
        403: {'description': 'User is not the group admin.'},
        404: {'description': 'Request not found.'}
    },
    description="Group Admin approves or rejects a pending join request.",
    tags=['Savings Groups']
)
class GroupRequestActionView(APIView):
    """
    Handles approval/rejection of group join requests.
    Employs pessimistic locking to prevent race conditions during group filling.
    """
    permission_classes = [IsAuthenticated, IsGroupAdmin]

    @transaction.atomic
    def post(self, request, pk):
        # Pessimistic Lock on the Request Row (prevents two threads from processing the SAME request simultaneously.)
        try:
            request_obj = GroupJoinRequest.objects.select_for_update(of=('self',)).select_related(
                'group__admin',
                'user__profile'
            ).get(pk=pk)
        except GroupJoinRequest.DoesNotExist:
            return Response({"error": "Join request not found."}, status=status.HTTP_404_NOT_FOUND)

        # Authorization Check
        if request_obj.group.admin != request.user:
            return Response({'detail': 'You are not authorized to handle this request.'},
                            status=status.HTTP_403_FORBIDDEN)

        # Status Validation
        if request_obj.status != 'pending':
            return Response({"error": f"Request is already {request_obj.status}."},
                            status=status.HTTP_400_BAD_REQUEST)

        # Input Validation
        serializer = GroupJoinActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data['action']

        # Lock the Group Row (prevents race condition where two different users fill the last spot.)
        group = SavingsGroup.objects.select_for_update().get(id=request_obj.group.id)

        if action == 'approve':
            # Capacity check inside the lock
            if group.current_members >= group.expected_members:
                return Response({'error': 'Cannot approve. Group is already full.'},
                                status=status.HTTP_400_BAD_REQUEST)

            try:
                # Create membership
                GroupMembership.objects.create(user=request_obj.user, group=group)

                # Increment member count
                group.current_members += 1
                group.save(update_fields=['current_members'])

                # Update Request Status
                request_obj.status = 'approved'
                request_obj.handled_by = request.user
                request_obj.handled_at = timezone.now()
                request_obj.save(update_fields=['status', 'handled_by', 'handled_at'])

                # Log Admin Action
                logger.info({
                    "event": "group_join_approved",
                    "admin_id": request.user.id,
                    "target_user_id": request_obj.user.id,
                    "group_id": group.id,
                    "request_id": getattr(request, 'request_id', None)
                })

                message = "User approved and added to the group."

                # Automatic Group Activation & Payout Order
                if group.current_members >= group.expected_members and not group.start_date:
                    group.start_date = timezone.now().date()
                    group.save(update_fields=['start_date'])

                    # Generate Rotation: Earliest joined members get paid first
                    memberships = GroupMembership.objects.filter(group=group).order_by('joined_at')
                    payout_objs = [
                        PayoutOrder(group=group, membership=m, position=idx)
                        for idx, m in enumerate(memberships, start=1)
                    ]
                    PayoutOrder.objects.bulk_create(payout_objs)

                    message += " Group is now full and payout cycles have been initialized."

                    logger.info({
                        "event": "group_activated",
                        "group_id": group.id,
                        "start_date": str(group.start_date),
                        "request_id": getattr(request, 'request_id', None)
                    })

                send_group_join_response_email_async.delay(pk, 'approved')

            except IntegrityError:
                return Response({"error": "User is already a member."}, status=400)

        elif action == 'reject':
            request_obj.status = 'rejected'
            request_obj.handled_by = request.user
            request_obj.handled_at = timezone.now()
            request_obj.save(update_fields=['status', 'handled_by', 'handled_at'])

            logger.info({
                "event": "group_join_rejected",
                "admin_id": request.user.id,
                "target_user_id": request_obj.user.id,
                "group_id": group.id,
                "request_id": getattr(request, 'request_id', None)
            })

            send_group_join_response_email_async.delay(pk, 'rejected')
            message = "User request has been rejected."

        return Response({"message": message}, status=status.HTTP_200_OK)

class DashboardView(APIView):
    permission_classes = [IsAuthenticated]
    @extend_schema(
        description="Retrieves the authenticated user's personalized dashboard.",
        tags=['User Dashboard'],
        responses={
            200: DashboardResponseSerializer,
            401: {'description': 'Authentication credentials were not provided.'}
        }
    )
    def get(self, request):
        user = request.user

        total_savings = (
            # All verified group contributions
            Contribution.objects.filter(
                membership__user=user,
                is_verified=True
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        ) + (
            # All verified goal contributions
            GoalContribution.objects.filter(
                goal__user=user,
                is_verified=True
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        )

        one_month_ago = timezone.now() - relativedelta(months=1)
        previous_period_savings = Contribution.objects.filter(
            membership__user=user,
            is_verified=True,
            paid_at__lt=one_month_ago
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        # Growth Percentage
        if previous_period_savings > 0:
            growth_percentage = ((total_savings - previous_period_savings) / previous_period_savings) * 100
        else:
            growth_percentage = 100.0 if total_savings > 0 else 0.0

        active_memberships = GroupMembership.objects.filter(
            user=user,
            group__status='active'
        ).select_related('group').prefetch_related(
            'group__memberships__contributions'
        )
        groups = [m.group for m in active_memberships]
        groups_serializer = GroupDashboardCardSerializer(
             groups, many=True, context={'request': request}
        )
        growth_val = round(float(growth_percentage), 1)

        return Response({
            "total_savings": total_savings,
            "growth_percentage": growth_val,
            "growth_text": f"{ '+' if growth_val >= 0 else '' }{growth_val}% from last month",
            "joined_groups": groups_serializer.data
        })

class ContributeView(APIView):
    """
    Handles contributions to a specific savings group.
    Employs pessimistic locking to prevent race conditions in financial balances.
    Rate limited by user to prevent transaction flooding.
    """
    permission_classes = [IsAuthenticated]
    @extend_schema(
        description="Manually record a contribution for the current cycle in an active savings group. "
                    "The user must be a member. Only one contribution per cycle is allowed.",
        tags=['Savings Groups'],
        request=None,
        responses={
            201: {'description': 'Contribution recorded successfully'},
            400: {'description': 'Insufficient funds or already contributed in this cycle.'},
            404: {'description': 'Group not found, not active, or user not a member.'},
        }
    )
    @idempotent_request
    @method_decorator(ratelimit(key='user', rate='5/m', method='POST', block=True))
    @transaction.atomic
    def post(self, request, group_id):

        # Ensure only verified users can move money
        if not request.user.is_verified:
            return Response(
                {"error": "You must complete KYC/Verification to contribute."},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            # Lock the group. Ensure cycle and status don't change.
            group = SavingsGroup.objects.select_for_update().get(id=group_id, status='active')

            # Lock the membership. Prevent concurrent hits for same group/user.
            membership = GroupMembership.objects.select_for_update().get(
                user=request.user,
                group=group
            )

            # Fetch and lock the user's wallet for balance update.
            wallet = Wallet.objects.select_for_update().get(user=request.user)
        except SavingsGroup.DoesNotExist:
            return Response({"error": "Group not found or not active"}, status=status.HTTP_404_NOT_FOUND)
        except GroupMembership.DoesNotExist:
            return Response({"error": "You are not a member of this group"}, status=status.HTTP_404_NOT_FOUND)
        except Wallet.DoesNotExist:
            return Response({"error": "User wallet not found"}, status=status.HTTP_404_NOT_FOUND)

        # Check if already contributed this cycle
        current_cycle = group.current_cycle_number
        if Contribution.objects.filter(membership=membership, cycle_number=current_cycle).exists():
            return Response(
                {"error": "You have already contributed for this cycle"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check for sufficient funds
        if wallet.current_balance < group.contribution_amount:
            return Response(
                {"error": "Insufficient wallet balance"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Record the Contribution record
        contribution = Contribution.objects.create(
            membership=membership,
            amount=group.contribution_amount,
            cycle_number=current_cycle,
            is_verified=True
        )

        # Record in Immutable Ledger
        LedgerService.transfer(
            amount=group.contribution_amount,
            transaction_type='contribution',
            description=f"Contribution to {group.group_name} (Cycle {current_cycle})",
            reference=f"CONTRIB-{contribution.id}-{uuid.uuid4().hex[:8]}",
            from_user=request.user,
            to_group=group,
            actor=request.user,
            request_id=getattr(request, 'request_id', None),
            related_group=group,
        )

        wallet.refresh_from_db()

        # Structured Audit Logging
        logger.info({
            "event": "group_contribution_recorded",
            "actor_id": request.user.id,
            "group_id": group.id,
            "amount": str(group.contribution_amount),
            "cycle": current_cycle,
            "new_balance": str(wallet.current_balance),
            "request_id": getattr(request, 'request_id', None)
        })

        return Response({
            "message": "Contribution recorded successfully",
            "contribution_id": contribution.id,
            "amount": contribution.amount,
            "cycle": current_cycle,
            "new_balance": wallet.current_balance
        }, status=status.HTTP_201_CREATED)


class CreateSavingsGoalView(APIView):
    """
    Allows authenticated users to create a new personal savings goal.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=SavingsGoalCreateSerializer,
        responses={
            201: inline_serializer(
                name='CreateGoalResponse',
                fields={
                    'success': rest_serializers.BooleanField(default=True),
                    'message': rest_serializers.CharField(),
                    'goal': SavingsGoalSerializer(),
                }
            )
        },
        description="Allows authenticated users to create a new personal savings goal.",
        tags=['Savings Goals']
    )
    @idempotent_request
    @transaction.atomic
    def post(self, request):
        serializer = SavingsGoalCreateSerializer(data=request.data, context={'request': request})

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            goal = serializer.save()
            return Response({
                "success": True,
                "message": "Savings goal created successfully!",
                "goal": SavingsGoalSerializer(goal).data
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Goal creation failed: {e}")
            return Response({
                "error": "Failed to create goal. Please try again."
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GoalsDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Retrieves the authenticated user's personalized goals dashboard.",
        tags=['Savings Goals'],
        responses={
            200: GoalsDashboardResponseSerializer,
            401: {'description': 'Authentication required.'}
        }
    )
    def get(self, request):
        user = request.user
        today = timezone.now().date()

        # Fetch goals once
        goals_queryset = SavingsGoal.objects.filter(user=user)

        total_target = goals_queryset.aggregate(total=Sum('target_amount'))['total'] or Decimal('0.00')

        # Calculate total saved across ALL goals in ONE query
        total_saved = GoalContribution.objects.filter(
            goal__user=user,
            is_verified=True
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        # Overall Progress
        overall_progress = 0.0
        if total_target > 0:
            overall_progress = float((total_saved / total_target) * 100)

        active_goals_count = 0
        for goal in goals_queryset:
            if goal.is_active:
                active_goals_count += 1

        goals_serializer = GoalDashboardCardSerializer(
            goals_queryset,
            many=True,
            context={'request': request}
        )

        return Response({
            "total_target": total_target,
            "total_saved": total_saved,
            "overall_progress": round(overall_progress, 1),
            "active_goals_count": active_goals_count,
            "goals": goals_serializer.data
        })

class ContributeToGoalView(APIView):
    """Handles contributions to a specific personal savings goal."""
    permission_classes = [IsAuthenticated]
    @extend_schema(
        description="Record a contribution to a personal goal. Prevents over-contribution.",
        tags=['Savings Goals'],
        responses={
            201: inline_serializer(
                name='GoalContributionSuccess',
                fields={
                    'message': serializers.CharField(),
                    'contribution_id': serializers.IntegerField(),
                    'amount': serializers.DecimalField(max_digits=12, decimal_places=2),
                    'new_saved': serializers.DecimalField(max_digits=12, decimal_places=2)
                }
            ),
            400: {'description': 'Goal completed or exceeds target amount.'},
            404: {'description': 'Goal not found.'}
        }
    )
    @idempotent_request
    @transaction.atomic
    def post(self, request, goal_id):
        try:
            goal = SavingsGoal.objects.select_for_update().get(id=goal_id, user=request.user)
        except SavingsGoal.DoesNotExist:
            return Response(
                {"error": "Goal not found or you do not own it."},
                status=status.HTTP_404_NOT_FOUND
            )

        current_saved = goal.current_saved

        # Check if goal is already completed
        if current_saved >= goal.target_amount:
            return Response(
                {"error": "This goal is already completed."},
                status=status.HTTP_400_BAD_REQUEST
            )

        contribution_amount = goal.regular_contribution
        if (current_saved + contribution_amount) > goal.target_amount:
            remaining = goal.target_amount - current_saved
            return Response(
                {
                    "error": f"Amount exceeds target. Only ₵{remaining} needed.",
                    "remaining_needed": remaining
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        contribution = GoalContribution.objects.create(
            goal=goal,
            amount=contribution_amount,
            is_verified=True
        )

        goal.last_contribution_date = timezone.now().date()
        goal.save(update_fields=['last_contribution_date'])

        # Record in Immutable Ledger (single-sided credit to personal wallet)
        LedgerService.create_entry(
            user=request.user,
            actor=request.user,
            amount=contribution.amount,
            direction='debit',
            transaction_type='goal_contribution',
            description=f"Deposit to goal: {goal.name}",
            reference=f"GOAL-{contribution.id}-{uuid.uuid4().hex[:8]}",
            request_id=getattr(request, 'request_id', None),
            related_goal=goal,
        )

        logger.info({
            "event": "goal_contribution_recorded",
            "user_id": request.user.id,
            "goal_id": goal.id,
            "amount": str(contribution.amount),
            "request_id": getattr(request, 'request_id', None)
        })

        return Response({
            "message": "Contribution recorded successfully",
            "contribution_id": contribution.id,
            "amount": contribution.amount,
            "new_saved": goal.current_saved
        }, status=status.HTTP_201_CREATED)

@extend_schema(
    tags=['Savings Goals'],
    summary="Manage a single savings goal (GET / PATCH / DELETE)",
    description="Authenticated owner can retrieve, partially update, or delete their goal (cascades to contributions).",
)
@method_decorator(never_cache, name='dispatch')
class GoalDetailView(RetrieveUpdateDestroyAPIView):
    """Implementation – atomic, observable, idempotent, heavily guarded."""

    serializer_class = SavingsGoalSerializer
    permission_classes = [IsAuthenticated, IsGoalOwner]
    lookup_field = 'id'
    parser_classes = [JSONParser]

    def get_queryset(self):
        # Double protection (queryset + permission)
        return SavingsGoal.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        if self.request.method in ('PATCH', 'PUT'):
            return SavingsGoalUpdateSerializer
        return super().get_serializer_class()

    @extend_schema(
        responses={200: SavingsGoalSerializer},
        description="Retrieve full goal details (including computed fields).",
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        request=SavingsGoalUpdateSerializer,
        responses={
            200: SavingsGoalSerializer,
            400: OpenApiExample("Validation failed", value={"target_date": ["must be in future"]}),
            409: {"description": "Idempotency conflict"}
        },
        examples=[
            OpenApiExample(
                "Update target & frequency",
                value={"target_amount": 15000, "frequency": "monthly"},
                request_only=True,
            )
        ],
    )
    @method_decorator(ratelimit(key='user', rate='20/m', block=True))
    @idempotent_request
    @transaction.atomic
    def update(self, request, *args, **kwargs):
        goal = self.get_object()
        goal = SavingsGoal.objects.select_for_update().get(pk=goal.pk)

        logger.info({
            "event": "savings_goal_update_started",
            "user_id": request.user.id,
            "goal_id": goal.id,
            "request_id": getattr(request, 'request_id', None),
            "idempotency_key": request.headers.get('X-Idempotency-Key')
        })

        response = super().update(request, *args, **kwargs)

        logger.info({
            "event": "savings_goal_updated",
            "user_id": request.user.id,
            "goal_id": goal.id,
            "changes": request.data,
            "new_target": str(goal.target_amount),
            "request_id": getattr(request, 'request_id', None)
        })

        return response

    @extend_schema(
        responses={
            204: OpenApiExample("Success", value={"message": "Goal and all contributions permanently deleted"}),
            404: {"description": "Goal not found or not owned"},
        },
        description="Hard delete + cascade. Irreversible. Audit logged.",
    )
    @method_decorator(ratelimit(key='user', rate='5/h', block=True))
    @idempotent_request
    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        goal = self.get_object()
        goal = SavingsGoal.objects.select_for_update().get(pk=goal.pk)

        contrib_count = goal.contributions.count()
        goal_name = goal.name
        goal_id = goal.id

        logger.info({
            "event": "savings_goal_delete_requested",
            "user_id": request.user.id,
            "goal_id": goal_id,
            "contributions_deleted": contrib_count,
            "request_id": getattr(request, 'request_id', None)
        })

        with transaction.atomic():
            GoalContribution.objects.filter(goal=goal).delete()
            goal.delete()

        logger.info({
            "event": "savings_goal_deleted",
            "user_id": request.user.id,
            "goal_id": goal_id,
            "goal_name": goal_name,
            "contributions_removed": contrib_count,
            "request_id": getattr(request, 'request_id', None)
        })

        return Response({
            "success": True,
            "message": f"Goal '{goal_name}' and {contrib_count} contribution(s) deleted permanently."
        }, status=status.HTTP_204_NO_CONTENT)

    def perform_update(self, serializer):
        serializer.save()

class GroupsStatsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Provides aggregated statistics for the user's active groups.",
        tags=['Savings Groups'],
        responses={
            200: GroupsStatsResponseSerializer,
            401: {'description': 'Authentication required.'}
        }
    )
    def get(self, request):
        user = request.user

        # Fetch active groups where user is a member
        active_groups = SavingsGroup.objects.filter(
            memberships__user=user,
            status='active'
        ).distinct()

        total_groups = active_groups.count()

        # Sum members across these groups
        total_members = active_groups.aggregate(
            total_members=Sum('current_members')
        )['total_members'] or 0

        # Calculate 'Current Pot' across all groups
        group_savings = Decimal('0.00')

        for group in active_groups.only('start_date', 'payout_interval_days', 'frequency'):
            current_cycle = group.current_cycle_number

            cycle_total = Contribution.objects.filter(
                membership__group=group,
                cycle_number=current_cycle,
                is_verified=True
            ).aggregate(
                total=Sum('amount')
            )['total'] or Decimal('0.00')

            group_savings += cycle_total

        return Response({
            'total_groups': total_groups,
            'total_members': total_members,
            'group_savings': group_savings
        })


@extend_schema(
    tags=['Analytics'],
    parameters=[
        OpenApiParameter(
            name='period',
            type=str,
            location=OpenApiParameter.QUERY,
            description='Time range for charts and growth',
            enum=['1month', '3months', '6months', '1year', 'all'],
            default='6months'
        )
    ],
    responses={200: AnalyticsResponseSerializer},
    description="Full analytics data for the dashboard. Includes time-range aware charts, growth metrics, dynamic insights & recommendations."
)
class AnalyticsView(APIView):
    permission_classes = [IsAuthenticated]

    def safe_sum(self, queryset):
        """Safely convert aggregate['total'] (which can be None) to Decimal(0)."""
        result = queryset.aggregate(total=Sum('amount'))['total']
        return result or Decimal('0.00')

    def get(self, request):
        period = request.query_params.get('period', '6months')
        months_map = {'1month': 1, '3months': 3, '6months': 6, '1year': 12, 'all': None}
        months_back = months_map.get(period, 6)

        user = request.user

        # === Wallet (only for distribution & recommendations) ===
        try:
            wallet = Wallet.objects.get(user=user)
            wallet_balance = wallet.current_balance
        except Wallet.DoesNotExist:
            wallet_balance = Decimal('0.00')

        # === Totals – matches your existing DashboardView (NO negative wallet) ===
        group_total = self.safe_sum(
            Contribution.objects.filter(membership__user=user, is_verified=True)
        )
        goal_total = self.safe_sum(
            GoalContribution.objects.filter(goal__user=user, is_verified=True)
        )
        total_savings = group_total + goal_total   # ← Correct total (only verified contributions)

        # === Growth Calculations (safe) ===
        one_month_ago = timezone.now() - relativedelta(months=1)
        two_months_ago = one_month_ago - relativedelta(months=1)

        previous_total = (
            self.safe_sum(Contribution.objects.filter(membership__user=user, is_verified=True, paid_at__lt=one_month_ago)) +
            self.safe_sum(GoalContribution.objects.filter(goal__user=user, is_verified=True, paid_at__lt=one_month_ago))
        )

        last_month_add = (
            self.safe_sum(Contribution.objects.filter(membership__user=user, is_verified=True, paid_at__gte=one_month_ago)) +
            self.safe_sum(GoalContribution.objects.filter(goal__user=user, is_verified=True, paid_at__gte=one_month_ago))
        )

        prev_month_add = (
            self.safe_sum(Contribution.objects.filter(membership__user=user, is_verified=True, paid_at__gte=two_months_ago, paid_at__lt=one_month_ago)) +
            self.safe_sum(GoalContribution.objects.filter(goal__user=user, is_verified=True, paid_at__gte=two_months_ago, paid_at__lt=one_month_ago))
        )

        monthly_growth_pct = ((last_month_add - prev_month_add) / prev_month_add * 100) if prev_month_add > 0 else 100.0

        # Active groups & goals progress
        active_groups = GroupMembership.objects.filter(user=user, group__status='active').count()

        total_target = SavingsGoal.objects.filter(user=user).aggregate(
            total=Sum('target_amount')
        )['total'] or Decimal('0.00')
        goals_progress = float((goal_total / total_target * 100)) if total_target > 0 else 0.0

        # === Savings Over Time (continuous months – no gaps) ===
        contrib_qs = Contribution.objects.filter(
            membership__user=user, is_verified=True
        ).annotate(month=TruncMonth('paid_at')).values('month').annotate(
            total=Sum('amount')
        ).order_by('month')

        goal_qs = GoalContribution.objects.filter(
            goal__user=user, is_verified=True
        ).annotate(month=TruncMonth('paid_at')).values('month').annotate(
            total=Sum('amount')
        ).order_by('month')

        monthly_dict = {}
        for item in list(contrib_qs) + list(goal_qs):
            m = item['month']
            monthly_dict[m] = monthly_dict.get(m, Decimal('0.00')) + item['total']

        if monthly_dict:
            all_months = sorted(monthly_dict.keys())
            current = all_months[0].replace(day=1)
            last = all_months[-1].replace(day=1)
            full_months = []
            while current <= last:
                full_months.append(current)
                current += relativedelta(months=1)
        else:
            full_months = []

        savings_over_time = []
        cumulative = Decimal('0.00')
        for m_start in full_months:
            contrib_this = monthly_dict.get(m_start, Decimal('0.00'))
            cumulative += contrib_this
            savings_over_time.append({
                'month': m_start.strftime('%b'),
                'amount': float(cumulative),
                'contributions': float(contrib_this)
            })

        if months_back is not None:
            savings_over_time = savings_over_time[-months_back:]

        # === Savings Distribution (clamped ≥ 0 so pie chart never breaks) ===
        savings_distribution = [
            {'name': 'Emergency Fund', 'value': float(max(Decimal('0.00'), wallet_balance))},
            {'name': 'Goals', 'value': float(goal_total)},
            {'name': 'Group Savings', 'value': float(group_total)},
        ]

        # === Group Performance ===
        groups_qs = SavingsGroup.objects.filter(
            memberships__user=user, status='active'
        ).distinct()
        group_performance = []
        for g in groups_qs:
            group_savings = self.safe_sum(
                Contribution.objects.filter(membership__group=g, is_verified=True)
            )
            group_performance.append({
                'name': g.group_name,
                'savings': float(group_savings),
                'members': g.current_members
            })

        # === Dynamic Key Insights & Recommendations (real data driven) ===
        # Top performing group
        sorted_groups = sorted(group_performance, key=lambda x: x.get('savings', 0), reverse=True)
        top_group = sorted_groups[0] if sorted_groups else {'name': 'No groups yet', 'savings': 0}

        avg_monthly_contribution = float(last_month_add)

        key_insights = [
            {
                "title": "Strong Savings Momentum",
                "description": f"You've increased your savings by {round(monthly_growth_pct, 1)}% in the last period"
            },
            {
                "title": "Consistent Contributions",
                "description": f"Your average monthly contribution is GHS {avg_monthly_contribution:,.0f}"
            },
            {
                "title": "Top Performing Group",
                "description": f'"{top_group["name"]}" has the highest total savings of GHS {top_group["savings"]:,.0f}'
            }
        ]

        # Rule-based recommendations (financial best practices)
        recommendations = []
        if goals_progress < 70:
            recommendations.append({
                "title": "Focus on Goals",
                "description": f"You're at {round(goals_progress, 1)}% progress. Increase your regular contribution to hit targets faster."
            })
        else:
            recommendations.append({
                "title": "You're Crushing Your Goals",
                "description": "Excellent progress! Consider raising your target_amount for new challenges."
            })

        if active_groups < 3:
            recommendations.append({
                "title": "Diversify Savings",
                "description": f"Join 1-2 more groups to spread risk and accelerate growth (you currently have {active_groups})."
            })
        else:
            recommendations.append({
                "title": "Well Diversified",
                "description": f"You have {active_groups} active groups – great portfolio balance!"
            })

        if wallet_balance < total_savings * Decimal('0.15'):
            recommendations.append({
                "title": "Boost Emergency Fund",
                "description": "Your cash reserve is low. Aim to keep at least 15% of total savings in your wallet."
            })
        else:
            recommendations.append({
                "title": "Solid Emergency Fund",
                "description": "Your wallet is healthy. Keep maintaining it."
            })

        # Ensure exactly 3 recommendations
        while len(recommendations) < 3:
            recommendations.append({
                "title": "Set New Goals",
                "description": "You're on track! Consider creating a new savings goal."
            })
        recommendations = recommendations[:3]

        # Final response
        data = {
            'stats': {
                'total_savings': total_savings,
                'monthly_growth': last_month_add,
                'monthly_growth_percentage': round(float(monthly_growth_pct), 1),
                'active_groups': active_groups,
                'goals_progress': round(goals_progress, 1),
            },
            'savings_over_time': savings_over_time,
            'savings_distribution': savings_distribution,
            'group_performance': group_performance,
            'key_insights': key_insights,
            'recommendations': recommendations,
        }

        return Response(data)

@extend_schema(
    description=(
        "Provides aggregated statistics for the 'Join Requests' admin dashboard cards: "
        "- Pending: Count of pending join requests across all groups the authenticated user administers. "
        "- Accepted: Count of approved requests. "
        "- Declined: Count of rejected requests."
    ),
    tags=['Savings Groups', 'Admin'],
    responses={200: JoinRequestsStatsSerializer},
    auth=['Bearer'],
)
class JoinRequestsStatsView(APIView):
    """
    Dedicated endpoint for group admins to get quick stats for the Join Requests page.
    Only accessible to users who are admins of at least one group.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        # Get all groups this user administers
        admin_groups = SavingsGroup.objects.filter(admin=user).values_list('id', flat=True)

        if not admin_groups:
            return Response({
                'pending': 0,
                'accepted': 0,
                'declined': 0
            })

        from django.db.models import Count, Q

        stats = GroupJoinRequest.objects.filter(
            group_id__in=admin_groups
        ).aggregate(
            pending=Count('id', filter=Q(status='pending')),
            accepted=Count('id', filter=Q(status='approved')),
            declined=Count('id', filter=Q(status='rejected')),
        )

        # Ensure all keys exist even if zero
        return Response({
            'pending': stats['pending'] or 0,
            'accepted': stats['accepted'] or 0,
            'declined': stats['declined'] or 0,
        })


@extend_schema(
    request=inline_serializer(
        name="ProfileUpdateRequest",
        fields={
            "email": serializers.EmailField(required=False),
            "full_name": serializers.CharField(required=False),
            "user_type": serializers.ChoiceField(choices=["student", "worker"], required=False),
            "ghana_post_address": serializers.CharField(required=False),
            "momo_provider": serializers.ChoiceField(choices=["mtn", "telecel", "airteltigo"], required=False),
            "momo_number": serializers.CharField(required=False),
            "momo_name": serializers.CharField(required=False),
        }
    ),
    responses={200: MeViewResponseSerializer},
    description="Update profile and MoMo payout details (partial update). Email is handled on the User model.",
    tags=['User Management']
)
class ProfileUpdateView(APIView):
    """L3-grade profile + payout update endpoint."""
    permission_classes = [IsAuthenticated]

    @idempotent_request
    @transaction.atomic
    def patch(self, request):
        user = request.user
        profile = user.profile
        data = request.data

        # Email update (with uniqueness check)
        if 'email' in data and data['email'] != user.email:
            new_email = data['email'].lower().strip()
            if User.objects.filter(email=new_email).exclude(pk=user.pk).exists():
                return Response({"error": "This email is already registered"}, status=status.HTTP_400_BAD_REQUEST)
            user.email = new_email
            user.save(update_fields=['email'])
            logger.info({"event": "email_changed", "user_id": user.id, "new_email": new_email})

        # Profile fields
        profile_fields = {'full_name', 'user_type', 'ghana_post_address', 'momo_provider', 'momo_number', 'momo_name'}
        profile_data = {k: v for k, v in data.items() if k in profile_fields}

        serializer = ProfileSerializer(profile, data=profile_data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        serializer.save()

        logger.info({
            "event": "profile_updated",
            "user_id": user.id,
            "updated_fields": list(profile_data.keys()),
            "request_id": getattr(request, 'request_id', None)
        })

        # Return same shape as /me/
        return Response({
            "user": {
                "id": user.id,
                "email": user.email,
                "is_verified": user.is_verified,
            },
            "profile": ProfileSerializer(profile).data
        })


@csrf_exempt
def run_daily_payouts(request):
    """Called by Vercel Cron → triggers the daily payout orchestrator"""
    if request.headers.get('X-Cron-Secret') != settings.QSTASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=401)

    process_daily_payouts.stash()
    return JsonResponse({"status": "daily payouts queued successfully"})


@csrf_exempt
def run_reconciliation(request):
    """Called by Vercel Cron → triggers financial integrity check"""
    if request.headers.get('X-Cron-Secret') != settings.QSTASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=401)

    reconcile_financial_integrity.stash()
    return JsonResponse({"status": "financial reconciliation queued successfully"})


@csrf_exempt
def run_clear_idempotency(request):
    """Called by Vercel Cron → cleans up old idempotency keys"""
    if request.headers.get('X-Cron-Secret') != settings.QSTASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=401)

    clear_old_idempotency_keys.stash()
    return JsonResponse({"status": "idempotency cleanup queued successfully"})


@csrf_exempt
def run_goal_reminders(request):
    """Called by Vercel Cron → sends daily goal reminders"""
    if request.headers.get('X-Cron-Secret') != settings.QSTASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=401)

    send_goal_reminders.stash()
    return JsonResponse({"status": "goal reminders queued successfully"})
