# accounts/views.py

import uuid
import hashlib
import logging
import json
from django.http import StreamingHttpResponse
from groq import Groq as GroqClient
import groq as groq_module
import cloudinary.uploader
from django.conf import settings
import secrets as secrets_module
from rest_framework.pagination import PageNumberPagination
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample, inline_serializer
from .models import GroupInviteLink, LedgerEntry, Notification, Wallet, SavingsGroup, Profile, GroupJoinRequest, GroupMembership, Contribution, PayoutOrder,  SavingsGoal, GoalContribution
from .tasks import clear_old_idempotency_keys, process_daily_payouts, reconcile_financial_integrity, send_dawurobo_otp_async, send_goal_reminders, verify_and_invalidate_otp_sync, send_group_join_request_email_async, send_group_join_response_email_async
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.views import TokenObtainPairView
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import serializers as rest_serializers
from django.views.decorators.cache import never_cache
from django.utils.decorators import method_decorator
from rest_framework.parsers import MultiPartParser, JSONParser
from django.db import transaction, IntegrityError, models
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
from .services import LedgerService, NotificationService
from .serializers import (
    AnalyticsResponseSerializer, ChangePasswordSerializer, GoalDashboardCardSerializer, SavingsGroupCreateSerializer, SavingsGroupSerializer, SendOTPSerializer, VerifyOTPSerializer,
    CustomTokenObtainPairSerializer, ForgotPasswordSerializer, ResetPasswordSerializer, ProfileSerializer,
    FullSignupSerializer, GroupJoinRequestSerializer, GroupJoinActionSerializer, GroupDashboardCardSerializer,
    DashboardResponseSerializer, SavingsGoalCreateSerializer, GoalsDashboardResponseSerializer,
    SavingsGoalSerializer, SavingsGoalUpdateSerializer, GroupsStatsResponseSerializer,
    JoinRequestsStatsSerializer, GroupJoinRequestCreateSerializer, NotificationSerializer
)

from django.db.models.functions import TruncMonth
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
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
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        login_field = serializer.validated_data['login_field'].lower().strip()

        try:
            if '@' in login_field:
                return Response(
                    {"error": "Please enter your registered phone number (not email)."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            else:
                # ← FIXED: Use the same secure hash lookup as login/reset/verify
                parsed_num = phonenumbers.parse(login_field, "GH")
                if phonenumbers.is_valid_number(parsed_num):
                    normalized_phone = phonenumbers.format_number(
                        parsed_num, phonenumbers.PhoneNumberFormat.E164
                    )
                else:
                    normalized_phone = login_field.strip()

                salt = getattr(settings, "HASH_SALT", settings.SECRET_KEY)
                hash_input = f"{normalized_phone}{salt}".encode('utf-8')
                phone_hash = hashlib.sha256(hash_input).hexdigest()

                user = User.objects.select_related('profile').get(
                    profile__momo_number_hash=phone_hash
                )
                momo_number = str(user.profile.momo_number)

        except (User.DoesNotExist, Profile.DoesNotExist):
            return Response(
                {"error": "User not found with this email or phone"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Send OTP
        result = send_dawurobo_otp_async(momo_number)
        if result.get("success"):
            # Nice masked phone for frontend UX
            masked_phone = f"{momo_number[:4]}•••{momo_number[-4:]}"
            return Response({
                "message": "OTP sent to your registered phone for password reset.",
                "phone": momo_number,      # used internally by frontend
                "masked_phone": masked_phone
            }, status=status.HTTP_200_OK)
        else:
            return Response(
                {"error": "Failed to send OTP. Try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


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

        # Email uniqueness check
        if User.objects.filter(email=email).exists():
            return Response({"error": "This email is already registered"}, status=status.HTTP_400_BAD_REQUEST)

        # Normalize the MoMo number to E.164 before hashing — mirrors Profile.save() exactly
        try:
            parsed_num = phonenumbers.parse(momo_number, "GH")
            normalized_momo = (
                phonenumbers.format_number(parsed_num, phonenumbers.PhoneNumberFormat.E164)
                if phonenumbers.is_valid_number(parsed_num)
                else momo_number
            )
        except Exception:
            normalized_momo = momo_number

        salt = getattr(settings, "HASH_SALT", settings.SECRET_KEY)
        momo_hash = hashlib.sha256(f"{normalized_momo}{salt}".encode("utf-8")).hexdigest()

        if Profile.objects.filter(momo_number_hash=momo_hash).exists():
            return Response(
                {"error": "This MoMo number is already registered"},
                status=status.HTTP_400_BAD_REQUEST
            )

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

        except IntegrityError as e:
            logger.error(f"Signup IntegrityError: {e}")
            # Safety net for race conditions that slip past the pre-checks above
            error_str = str(e).lower()
            if 'momo_number_hash' in error_str:
                return Response(
                    {"error": "This MoMo number is already registered"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if 'email' in error_str:
                return Response(
                    {"error": "This email is already registered"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            return Response(
                {"error": "Registration failed due to a conflict. Please check your details and try again."},
                status=status.HTTP_400_BAD_REQUEST
            )

        except Exception as e:
            error_str = str(e)
            logger.error(f"Signup Transaction Failed: {error_str}")

            if error_str == "SMS provider error":
                return Response(
                    {"error": "Account created but we could not send your OTP. Please use 'Resend OTP' to try again."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            return Response(
                {"error": "Registration failed. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

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


# class FullSignupView(APIView):
#     permission_classes = [permissions.AllowAny]
#     parser_classes = [MultiPartParser]

#     @extend_schema(description="Register user and profile. Triggers OTP.")
#     @transaction.atomic
#     @idempotent_request
#     def post(self, request):
#         data = request.data

#         required_fields = [
#             'email', 'password', 'password2', 'full_name', 'date_of_birth',
#             'user_type', 'ghana_post_address', 'momo_provider', 'momo_number', 'momo_name'
#         ]
#         for field in required_fields:
#             if not data.get(field):
#                 return Response(
#                     {"error": f"{field.replace('_', ' ').title()} is required"},
#                     status=status.HTTP_400_BAD_REQUEST
#                 )

#         if data['password'] != data['password2']:
#             return Response({"error": "Passwords do not match"}, status=status.HTTP_400_BAD_REQUEST)

#         email = data['email'].lower().strip()
#         momo_number = str(data['momo_number']).strip()

#         if User.objects.filter(email=email).exists():
#             return Response({"error": "This email is already registered"}, status=status.HTTP_400_BAD_REQUEST)
#         if Profile.objects.filter(momo_number=momo_number).exists():
#             return Response({"error": "This MoMo number is already registered"}, status=status.HTTP_400_BAD_REQUEST)

#         # Handle File Upload
#         profile_picture_url = None
#         if 'profile_picture' in request.FILES:
#             try:
#                 upload_result = cloudinary.uploader.upload(
#                     request.FILES['profile_picture'],
#                     folder="snappx/profiles/",
#                     transformation=[
#                         {'width': 500, 'height': 500, 'crop': 'limit'},
#                         {'quality': "auto"}
#                     ]
#                 )
#                 profile_picture_url = upload_result.get('secure_url')
#             except Exception as e:
#                 logger.warning(f"Cloudinary upload failed: {e}")

#         try:
#             with transaction.atomic():
#                 user = User.objects.create_user(
#                     email=email,
#                     username=email.split('@')[0],
#                     password=data['password'],
#                     is_verified=False
#                 )

#                 # Create Profile
#                 Profile.objects.create(
#                     user=user,
#                     full_name=data['full_name'],
#                     date_of_birth=data['date_of_birth'],
#                     user_type=data['user_type'],
#                     ghana_post_address=data['ghana_post_address'],
#                     profile_picture=profile_picture_url,
#                     momo_provider=data['momo_provider'],
#                     momo_number=momo_number,
#                     momo_name=data['momo_name']
#                 )

#                 otp_result = send_dawurobo_otp_async(momo_number)
#                 if not otp_result.get("success"):
#                     raise Exception("SMS provider error")

#         except Exception as e:
#             logger.error(f"Signup Transaction Failed: {str(e)}")
#             return Response({"error": "Registration failed. Please try again later."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

#         logger.info({
#             "event": "user_registered",
#             "user_id": user.id,
#             "momo_mask": f"***-{momo_number[-4:]}",
#             "wallet_initialized": True
#         })

#         return Response({
#             "message": "Account created successfully! OTP sent.",
#             "phone": momo_number,
#             "next_step": "verify_otp"
#         }, status=status.HTTP_201_CREATED)


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
    responses={200: SavingsGroupSerializer(many=True)}
)
class MyJoinedGroupsListView(generics.ListAPIView):
    serializer_class = SavingsGroupSerializer
    permission_classes = [IsAuthenticated]

    # ← ADD THIS METHOD (this was the missing piece)
    def get_serializer_context(self):
        return {'request': self.request}

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

class GroupListPagination(PageNumberPagination):
    """
    8 groups per page for the public 'browse & join' list.
    Callers can override via ?page_size=N (capped at 20).
    """
    page_size            = 8
    page_size_query_param = 'page_size'
    max_page_size        = 20


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
        OpenApiParameter(
            name='page',
            type={'type': 'integer'},
            location=OpenApiParameter.QUERY,
            description='Page number (8 groups per page).'
        ),
        OpenApiParameter(
            name='page_size',
            type={'type': 'integer'},
            location=OpenApiParameter.QUERY,
            description='Override page size (max 20).'
        ),
    ],
    examples=[
        OpenApiExample(
            name='Filter and Search Example',
            description='Retrieve all weekly groups with "vacation" in the name/description.',
            value={'search': 'vacation', 'frequency': 'weekly'},
            request_only=True
        ),
    ],
    responses={
        200: SavingsGroupSerializer(many=True),
        401: {'description': 'Authentication credentials were not provided.'}
    }
)
class AllGroupsListView(generics.ListAPIView):
    """Lists all active savings groups for the platform, with filtering, searching, and pagination."""
    serializer_class    = SavingsGroupSerializer
    permission_classes  = [IsAuthenticated]
    pagination_class    = GroupListPagination
    filter_backends     = [DjangoFilterBackend, SearchFilter]
    filterset_fields    = ['frequency', 'expected_members', 'contribution_amount']
    search_fields       = ['group_name', 'description']

    def get_queryset(self):
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
        description="Submit a request to join an active savings group. Optionally include a 'reason' to help the admin decide.",
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

        # 1. NEW: Check if the user is the admin of the group
        if group.admin == user:
            return Response(
                {"error": "You are the admin of this group. You do not need to send a join request."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 2. Check if already an approved member
        if GroupMembership.objects.filter(user=user, group=group).exists():
            return Response(
                {"error": "You are already a member of this group."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 3. Check existing request status
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

                # Notify the group admin of the re-submitted request
                transaction.on_commit(lambda: NotificationService.notify_join_request(existing_request))

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

            # Notify the group admin of the new join request
            transaction.on_commit(lambda: NotificationService.notify_join_request(new_request))

            try:
                send_group_join_request_email_async.delay(new_request.id)
            except Exception as e:
                logger.error(f"Failed to queue group join email: {e}")

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
        # 1. Pessimistic Lock on the Request Row
        # Prevents two threads from processing the SAME request simultaneously.
        try:
            request_obj = GroupJoinRequest.objects.select_for_update(of=('self',)).select_related(
                'group__admin',
                'user__profile'
            ).get(pk=pk)
        except GroupJoinRequest.DoesNotExist:
            return Response({"error": "Join request not found."}, status=status.HTTP_404_NOT_FOUND)

        # 2. Authorization Check
        if request_obj.group.admin != request.user:
            return Response({'detail': 'You are not authorized to handle this request.'},
                            status=status.HTTP_403_FORBIDDEN)

        # 3. Status Validation
        if request_obj.status != 'pending':
            return Response({"error": f"Request is already {request_obj.status}."},
                            status=status.HTTP_400_BAD_REQUEST)

        # 4. Input Validation
        serializer = GroupJoinActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data['action']

        # 5. Lock the Group Row
        # Prevents race condition where two different admins/threads fill the last spot at once.
        group = SavingsGroup.objects.select_for_update().get(id=request_obj.group.id)

        if action == 'approve':
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

                # Notify the applicant that their request was approved
                transaction.on_commit(lambda: NotificationService.notify_join_approved(request_obj))
                # Notify existing group members about the new member
                transaction.on_commit(lambda: NotificationService.notify_new_member(request.user, request_obj.user, group))

                message = "User approved and added to the group."

                # 6. Automatic Group Activation & Payout Order
                is_activating = False
                if group.current_members >= group.expected_members and not group.start_date:
                    is_activating = True
                    group.start_date = timezone.now().date()
                    group.save(update_fields=['start_date'])

                    # FIX: Clear any existing payout orders before creating new ones.
                    # This prevents IntegrityErrors if the activation logic retries.
                    PayoutOrder.objects.filter(group=group).delete()

                    # Generate Rotation: Earliest joined members get paid first
                    memberships = GroupMembership.objects.filter(group=group).order_by('joined_at')
                    payout_objs = [
                        PayoutOrder(group=group, membership=m, position=idx)
                        for idx, m in enumerate(memberships, start=1)
                    ]
                    PayoutOrder.objects.bulk_create(payout_objs)
                    message += " Group is now full and payout cycles have been initialized."

                    # Notify all members that the group is now active
                    transaction.on_commit(lambda: NotificationService.notify_group_activated(group))

                # 7. SUCCESS CALLBACKS (Only run if transaction commits)
                # This prevents "Ghost Logs" where the log says success but the DB rolled back.
                transaction.on_commit(lambda: logger.info({
                    "event": "group_join_approved",
                    "admin_id": request.user.id,
                    "target_user_id": request_obj.user.id,
                    "group_id": group.id,
                    "request_id": getattr(request, 'request_id', None)
                }))

                if is_activating:
                    transaction.on_commit(lambda: logger.info({
                        "event": "group_activated",
                        "group_id": group.id,
                        "start_date": str(group.start_date),
                        "request_id": getattr(request, 'request_id', None)
                    }))

                transaction.on_commit(lambda: send_group_join_response_email_async.delay(pk, 'approved'))

            except IntegrityError:
                # Usually triggered if GroupMembership uniqueness (user, group) is violated
                return Response({"error": "User is already a member or a conflict occurred."},
                                status=status.HTTP_400_BAD_REQUEST)

        elif action == 'reject':
            request_obj.status = 'rejected'
            request_obj.handled_by = request.user
            request_obj.handled_at = timezone.now()
            request_obj.save(update_fields=['status', 'handled_by', 'handled_at'])

            # Notify the applicant that their request was rejected
            transaction.on_commit(lambda: NotificationService.notify_join_rejected(request_obj))

            transaction.on_commit(lambda: logger.info({
                "event": "group_join_rejected",
                "admin_id": request.user.id,
                "target_user_id": request_obj.user.id,
                "group_id": group.id,
                "request_id": getattr(request, 'request_id', None)
            }))

            transaction.on_commit(lambda: send_group_join_response_email_async.delay(pk, 'rejected'))
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

        # Check if this contribution completed the goal
        goal.refresh_from_db()
        if goal.current_saved >= goal.target_amount:
            transaction.on_commit(lambda: NotificationService.notify_goal_completed(goal))

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

    process_daily_payouts.delay()
    return JsonResponse({"status": "daily payouts queued successfully"})


@csrf_exempt
def run_reconciliation(request):
    """Called by Vercel Cron → triggers financial integrity check"""
    if request.headers.get('X-Cron-Secret') != settings.QSTASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=401)

    reconcile_financial_integrity.delay()
    return JsonResponse({"status": "financial reconciliation queued successfully"})


@csrf_exempt
def run_clear_idempotency(request):
    """Called by Vercel Cron → cleans up old idempotency keys"""
    if request.headers.get('X-Cron-Secret') != settings.QSTASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=401)

    clear_old_idempotency_keys.delay()
    return JsonResponse({"status": "idempotency cleanup queued successfully"})


@csrf_exempt
def run_goal_reminders(request):
    """Called by Vercel Cron → sends daily goal reminders"""
    if request.headers.get('X-Cron-Secret') != settings.QSTASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=401)

    send_goal_reminders.delay()
    return JsonResponse({"status": "goal reminders queued successfully"})



SNAPPX_SYSTEM_PROMPT = """You are SnappX AI — a warm, sharp financial assistant built for everyday Ghanaians.
SnappX is a susu/savings app where users create group savings circles, set personal goals, and contribute regularly.

Your personality:
- Friendly and encouraging, like a knowledgeable older sibling
- Concrete and practical — always give actionable steps
- Use Ghanaian context: mention cedis (₵), MoMo, susu groups, chop money, etc. naturally
- Keep responses concise (3-5 bullet points or short paragraphs max) unless asked for detail
- End with a question or next step to keep conversation flowing

You help with:
- Budgeting and expense tracking
- Savings strategies (personal goals and group susu)
- Investment basics (treasury bills, fixed deposits, stocks)
- How to use SnappX features (groups, goals, payouts)
- General financial literacy for the Ghanaian context

Never give specific investment advice or guarantees. Always recommend consulting a licensed financial advisor for complex decisions."""

class AIChatView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        messages = request.data.get("messages", [])

        if not messages or not isinstance(messages, list):
            return Response(
                {"error": "A 'messages' list is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(messages) > 50:
            return Response(
                {"error": "Conversation history too long. Please start a new chat."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        for msg in messages:
            if msg.get("role") not in ("user", "assistant") or not msg.get("content"):
                return Response(
                    {"error": "Each message must have a 'role' (user/assistant) and 'content'."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if messages[0]["role"] != "user":
            return Response(
                {"error": "Conversation must begin with a user message."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        api_key = settings.GROQ_API_KEY
        if not api_key:
            logger.error("GROQ_API_KEY is not configured.")
            return Response(
                {"error": "AI service is not configured. Please contact support."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        def event_stream():
            try:
                client = GroqClient(api_key=api_key)

                # Prepend system prompt as a system message
                groq_messages = [
                    {"role": "system", "content": SNAPPX_SYSTEM_PROMPT}
                ] + [
                    {"role": m["role"], "content": m["content"]}
                    for m in messages
                ]

                stream = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=groq_messages,
                    max_tokens=1000,
                    stream=True,
                )

                for chunk in stream:
                    content = chunk.choices[0].delta.content
                    if content:
                        import json
                        yield f"data: {json.dumps(content)}\n\n"

                yield "data: [DONE]\n\n"

            except groq_module.RateLimitError:
                logger.warning(f"Groq rate limit hit for user {request.user.id}")
                yield "data: [ERROR] Rate limit reached. Please try again shortly.\n\n"

            except groq_module.AuthenticationError:
                logger.error("Groq API key is invalid.")
                yield "data: [ERROR] Authentication failed\n\n"

            except groq_module.APIConnectionError as exc:
                logger.error(f"Groq connection error: {exc}")
                yield "data: [ERROR] Could not connect to AI service. Please try again.\n\n"

            except groq_module.APIStatusError as exc:
                logger.error(f"Groq API error {exc.status_code}: {exc.message}")
                yield "data: [ERROR] AI service error. Please try again.\n\n"

            except Exception as exc:
                logger.error(f"Unexpected error in AI chat stream: {exc}")
                yield "data: [ERROR] Something went wrong. Please try again.\n\n"

        response = StreamingHttpResponse(
            event_stream(),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response


class NotificationListView(APIView):
    """
    GET /accounts/notifications/
    Returns paginated notifications for the authenticated user.

    Query params:
      ?type=join_request|payout_received|...   filter by type
      ?is_read=true|false                      filter by read status
      ?limit=20&offset=0                       pagination
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .models import Notification
        from .serializers import NotificationSerializer

        qs = Notification.objects.filter(
            user=request.user
        ).select_related('related_group', 'related_goal')

        # Filtering
        notif_type = request.query_params.get('type')
        is_read    = request.query_params.get('is_read')
        limit      = int(request.query_params.get('limit', 20))
        offset     = int(request.query_params.get('offset', 0))

        if notif_type and notif_type != 'all':
            qs = qs.filter(notification_type=notif_type)
        if is_read is not None:
            qs = qs.filter(is_read=(is_read.lower() == 'true'))

        total_count  = qs.count()
        unread_count = Notification.objects.filter(user=request.user, is_read=False).count()

        page = qs[offset: offset + limit]
        data = NotificationSerializer(page, many=True).data

        return Response({
            'results':      data,
            'unread_count': unread_count,
            'total_count':  total_count,
            'has_more':     (offset + limit) < total_count,
        })


class NotificationMarkReadView(APIView):
    """POST /accounts/notifications/<pk>/read/"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        from .models import Notification
        try:
            n = Notification.objects.get(pk=pk, user=request.user)
            n.mark_as_read()
            return Response({'success': True, 'id': pk})
        except Notification.DoesNotExist:
            return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)


class NotificationMarkAllReadView(APIView):
    """POST /accounts/notifications/mark-all-read/"""
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        from .models import Notification
        now   = timezone.now()
        count = Notification.objects.filter(
            user=request.user, is_read=False
        ).update(is_read=True, read_at=now)
        return Response({'success': True, 'marked_count': count})


class NotificationUnreadCountView(APIView):
    """GET /accounts/notifications/unread-count/  (cheap poll endpoint)"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .models import Notification
        count = Notification.objects.filter(user=request.user, is_read=False).count()
        return Response({'unread_count': count})


class NotificationDeleteView(APIView):
    """DELETE /accounts/notifications/<pk>/"""
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        from .models import Notification
        try:
            n = Notification.objects.get(pk=pk, user=request.user)
            n.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Notification.DoesNotExist:
            return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)


class NotificationClearAllView(APIView):
    """DELETE /accounts/notifications/  — wipe all of the user's notifications"""
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        from .models import Notification
        count, _ = Notification.objects.filter(user=request.user).delete()
        return Response({'success': True, 'deleted_count': count})


class RecentActivityView(APIView):
    """
    GET /accounts/activity/
    Returns the last N ledger entries for the authenticated user.
    These are real, immutable financial events — contributions, payouts, goal deposits.

    Query params:
      ?limit=10    How many to return (default 10, max 50)
      ?offset=0    Pagination offset
      ?type=contribution|payout|goal_contribution|all   Filter by type
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        limit  = min(int(request.query_params.get('limit', 10)), 50)
        offset = int(request.query_params.get('offset', 0))
        tx_type = request.query_params.get('type', 'all')

        # Get the user's wallet
        try:
            wallet = request.user.wallet
        except Exception:
            return Response({'results': [], 'total': 0, 'has_more': False})

        qs = LedgerEntry.objects.filter(
            wallet=wallet
        ).select_related(
            'related_group', 'related_goal', 'actor__profile'
        ).order_by('-created_at')

        if tx_type != 'all':
            qs = qs.filter(transaction_type=tx_type)

        total  = qs.count()
        page   = qs[offset: offset + limit]

        results = []
        for entry in page:
            results.append({
                'id':               str(entry.id),
                'type':             entry.transaction_type,
                'direction':        entry.direction,
                'amount':           str(entry.amount),
                'description':      entry.description,
                'reference':        entry.reference,
                'created_at':       entry.created_at.isoformat(),
                'related_group_id':   entry.related_group_id,
                'related_group_name': entry.related_group.group_name if entry.related_group else None,
                'related_group_public_id': entry.related_group.public_id if entry.related_group else None,
                'related_goal_id':    entry.related_goal_id,
                'related_goal_name':  entry.related_goal.name if entry.related_goal else None,
            })

        return Response({
            'results':  results,
            'total':    total,
            'has_more': (offset + limit) < total,
        })


class ScheduledContributionsView(APIView):
    """
    GET /accounts/scheduled-contributions/
    Returns upcoming group contributions for the current user.
    Each item tells the frontend: group name, amount, frequency, days until due,
    whether the user has already contributed this cycle, and whether they have
    enough wallet balance.

    This is derived from the user's active memberships + group cycle metadata.
    No extra DB table needed — pure computation from existing models.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user    = request.user
        today   = timezone.now().date()

        # Wallet balance for sufficiency check
        try:
            wallet_balance = user.wallet.current_balance
        except Exception:
            wallet_balance = 0

        memberships = GroupMembership.objects.filter(
            user=user,
            group__status='active',
        ).select_related(
            'group__admin__profile'
        ).prefetch_related('contributions')

        results = []

        for membership in memberships:
            group = membership.group

            # Has the user already contributed this cycle?
            current_cycle = group.current_cycle_number
            already_contributed = membership.contributions.filter(
                cycle_number=current_cycle,
                is_verified=True,
            ).exists()

            # When is the next contribution due?
            # Due date = start of the current cycle + interval
            next_payout = group.next_payout_date   # the deadline for current cycle

            if next_payout:
                days_until_due = (next_payout - today).days
                days_until_due = max(0, days_until_due)
            else:
                days_until_due = None

            # Status logic
            if already_contributed:
                contribution_status = 'completed'
            elif days_until_due is not None and days_until_due <= 2:
                contribution_status = 'due_soon'
            elif days_until_due == 0:
                contribution_status = 'overdue'
            else:
                contribution_status = 'upcoming'

            has_sufficient_balance = wallet_balance >= group.contribution_amount

            results.append({
                'group_id':              group.id,
                'group_name':            group.group_name,
                'group_public_id':       group.public_id,
                'amount':                str(group.contribution_amount),
                'frequency':             group.frequency,
                'current_cycle':         current_cycle,
                'next_due_date':         next_payout.isoformat() if next_payout else None,
                'days_until_due':        days_until_due,
                'already_contributed':   already_contributed,
                'status':                contribution_status,
                'has_sufficient_balance': has_sufficient_balance,
                'wallet_balance':        str(wallet_balance),
                'total_members':         group.current_members,
                'contribution_amount':   str(group.contribution_amount),
            })

        # Sort: overdue first, then due_soon, then upcoming, then completed
        order = {'overdue': 0, 'due_soon': 1, 'upcoming': 2, 'completed': 3}
        results.sort(key=lambda x: (order.get(x['status'], 99), x.get('days_until_due') or 9999))

        total_upcoming_amount = sum(
            float(r['amount']) for r in results
            if r['status'] in ('upcoming', 'due_soon', 'overdue')
        )

        return Response({
            'contributions':        results,
            'total_upcoming_amount': str(total_upcoming_amount),
            'wallet_balance':       str(wallet_balance),
        })


class GroupInviteLinkView(APIView):
    """
    GET  /accounts/groups/<group_id>/invite/
         Any member or the admin fetches (and auto-creates) the invite link.
         <group_id> accepts EITHER the public_id (19-digit string) OR the
         legacy numeric PK for backward compatibility.

    POST /accounts/groups/<group_id>/invite/
         Admin-only: regenerate the token (invalidates all existing links).
    """
    permission_classes = [IsAuthenticated]

    def _get_group(self, group_id_str: str):
        """
        Resolves a group from either its public_id or its integer PK.
        Returns the SavingsGroup instance or raises Http404.
        """
        from rest_framework.exceptions import NotFound

        # 1. Try public_id first (the normal case for new groups)
        group = SavingsGroup.objects.filter(public_id=group_id_str).first()
        if group:
            return group

        # 2. Legacy fallback: numeric PK
        if str(group_id_str).isdigit():
            group = SavingsGroup.objects.filter(id=int(group_id_str)).first()
            if group:
                return group

        raise NotFound({"error": "Group not found."})

    def _resolve_group_and_check_access(self, request, group_id):
        group = self._get_group(str(group_id))

        is_admin  = group.admin_id == request.user.id
        is_member = GroupMembership.objects.filter(
            user=request.user, group=group
        ).exists()

        if not is_admin and not is_member:
            return None, Response(
                {"error": "You must be a member of this group."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return group, None

    def get(self, request, group_id):
        from .models import GroupInviteLink

        group, err = self._resolve_group_and_check_access(request, group_id)
        if err:
            return err

        invite, _ = GroupInviteLink.objects.get_or_create(
            group=group,
            defaults={"created_by": request.user, "is_active": True},
        )

        frontend_url = getattr(settings, "FRONTEND_URL", settings.SITE_URL)
        invite_url   = f"{frontend_url}/invite/{invite.token}"

        return Response({
            "token":       invite.token,
            "invite_url":  invite_url,
            "is_active":   invite.is_active,
            "click_count": invite.click_count,
            "created_at":  invite.created_at.isoformat(),
        })

    def post(self, request, group_id):
        """Regenerate — admin only."""
        from .models import GroupInviteLink
        import secrets as _secrets

        # Resolve group the same way (public_id or PK)
        group = self._get_group(str(group_id))

        if group.admin_id != request.user.id:
            return Response(
                {"error": "Group not found or you are not the admin."},
                status=status.HTTP_403_FORBIDDEN,
            )

        invite, _ = GroupInviteLink.objects.get_or_create(
            group=group,
            defaults={"created_by": request.user},
        )
        invite.token     = _secrets.token_urlsafe(32)
        invite.is_active = True
        invite.save(update_fields=["token", "is_active", "updated_at"])

        frontend_url = getattr(settings, "FRONTEND_URL", settings.SITE_URL)
        invite_url   = f"{frontend_url}/invite/{invite.token}"

        logger.info({
            "event":    "invite_link_regenerated",
            "admin_id": request.user.id,
            "group_id": group.id,
        })

        return Response({
            "token":      invite.token,
            "invite_url": invite_url,
            "message":    "Invite link regenerated. Previous links are now invalid.",
        })

class InvitePreviewView(APIView):
    """
    Public (AllowAny) — returns safe group info for the invite landing page.
    Atomically increments click_count on every hit.
    """
    permission_classes = [AllowAny]

    def get(self, request, token):
        from .models import GroupInviteLink

        try:
            invite = GroupInviteLink.objects.select_related(
                "group__admin__profile"
            ).get(token=token, is_active=True)
        except GroupInviteLink.DoesNotExist:
            return Response(
                {"error": "This invite link is invalid or has expired."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Atomic increment — safe under concurrent load
        GroupInviteLink.objects.filter(pk=invite.pk).update(
            click_count=models.F("click_count") + 1
        )

        group          = invite.group
        spots_remaining = max(0, group.expected_members - group.current_members)

        return Response({
            "valid":               True,
            "group_id":            group.id,
            "public_id":           group.public_id,
            "group_name":          group.group_name,
            "description":         group.description,
            "contribution_amount": str(group.contribution_amount),
            "frequency":           group.frequency,
            "current_members":     group.current_members,
            "expected_members":    group.expected_members,
            "admin_name":          group.admin.profile.full_name,
            "admin_photo":         group.admin.profile.profile_picture,
            "status":              group.status,
            "spots_remaining":     spots_remaining,
            "is_full":             spots_remaining == 0,
        })

class InviteAcceptView(APIView):
    """
    POST /accounts/invite/<token>/accept/

    Authenticated user auto-joins the group that owns this invite token.
    Bypasses the GroupJoinRequest / admin-approval cycle entirely.
    The invite link must be active and the group must have capacity.

    Returns 200 if user was already a member (idempotent).
    Returns 201 on successful auto-join.
    """
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, token):
        # ── 1. Validate the invite token
        try:
            invite = GroupInviteLink.objects.select_related(
                'group__admin__profile'
            ).get(token=token, is_active=True)
        except GroupInviteLink.DoesNotExist:
            return Response(
                {"error": "This invite link is invalid or has expired."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # ── 2. Lock the group row to prevent concurrent capacity overshoots ───
        group = SavingsGroup.objects.select_for_update().get(pk=invite.group_id)
        user  = request.user

        # ── 3. Idempotency: admin is already implicitly a member ──────────────
        if group.admin_id == user.id:
            return Response({
                "already_member": True,
                "message": "You are the admin of this group.",
                "group_public_id": group.public_id,
                "group_id": group.id,
                "group_name": group.group_name,
            }, status=status.HTTP_200_OK)

        # ── 4. Idempotency: already a regular member ──────────────────────────
        if GroupMembership.objects.filter(user=user, group=group).exists():
            return Response({
                "already_member": True,
                "message": "You are already a member of this group.",
                "group_public_id": group.public_id,
                "group_id": group.id,
                "group_name": group.group_name,
            }, status=status.HTTP_200_OK)

        # ── 5. Capacity guard ─────────────────────────────────────────────────
        if group.current_members >= group.expected_members:
            return Response(
                {"error": "Sorry, this group is already full."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── 6. Status guard (allow pending + active; block completed/rejected) ─
        if group.status not in ('pending', 'active'):
            return Response(
                {"error": "This group is not currently accepting new members."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── 7. Auto-join: create GroupMembership directly ─────────────────────
        try:
            GroupMembership.objects.create(user=user, group=group)
        except IntegrityError:
            # Concurrent request already created the membership — treat as OK
            return Response({
                "already_member": True,
                "message": "You are already a member of this group.",
                "group_public_id": group.public_id,
                "group_id": group.id,
                "group_name": group.group_name,
            }, status=status.HTTP_200_OK)

        # Increment member count (row is already locked via select_for_update)
        group.current_members += 1
        group.save(update_fields=['current_members'])

        # ── 8. Auto-close any pending join request the user may have sent ─────
        GroupJoinRequest.objects.filter(
            user=user, group=group, status='pending'
        ).update(
            status='approved',
            handled_at=timezone.now(),
            handled_by=group.admin,
        )

        # ── 9. Notifications (after commit — never block the response) ─────────
        _group = group
        _user  = user

        def _notify():
            try:
                NotificationService._create(
                    user=_user,
                    notification_type='join_approved',
                    title='You joined via invite! 🎉',
                    message=(
                        f'You\'ve been added to "{_group.group_name}" '
                        f'via an invite link. Welcome aboard!'
                    ),
                    related_group=_group,
                    action_url='Groups',
                    metadata={
                        'group_id':         _group.id,
                        'group_name':       _group.group_name,
                        'via_invite_link':  True,
                    },
                )
                NotificationService.notify_new_member(_group.admin, _user, _group)
            except Exception as exc:
                logger.error(f"[InviteAcceptView] Notification error: {exc}")

        transaction.on_commit(_notify)

        # ── 10. Auto-activate if the group just reached full capacity ──────────
        is_activating = (
            group.current_members >= group.expected_members
            and not group.start_date
        )

        if is_activating:
            group.start_date = timezone.now().date()
            group.save(update_fields=['start_date'])

            # Rebuild the payout rotation in join-order
            PayoutOrder.objects.filter(group=group).delete()
            memberships = GroupMembership.objects.filter(
                group=group
            ).order_by('joined_at')
            payout_objs = [
                PayoutOrder(group=group, membership=m, position=idx)
                for idx, m in enumerate(memberships, start=1)
            ]
            PayoutOrder.objects.bulk_create(payout_objs)

            transaction.on_commit(
                lambda: NotificationService.notify_group_activated(_group)
            )

        # ── 11. Audit log ─────────────────────────────────────────────────────
        logger.info({
            "event":           "invite_link_auto_join",
            "user_id":         user.id,
            "group_id":        group.id,
            "group_activated": is_activating,
            "request_id":      getattr(request, 'request_id', None),
        })

        return Response({
            "message":          f"Welcome to {group.group_name}! You have been added as a member.",
            "already_member":   False,
            "group_public_id":  group.public_id,
            "group_id":         group.id,
            "group_name":       group.group_name,
        }, status=status.HTTP_201_CREATED)
