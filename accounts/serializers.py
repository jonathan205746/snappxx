# accounts/serializers.py

import os
import html
import hashlib
import phonenumbers
from time import timezone
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from .models import GroupAdminKYC, Profile, SavingsGroup, GroupJoinRequest, GroupMembership, Contribution, SavingsGoal, GoalContribution
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model
from rest_framework import serializers
from django.conf import settings
from django.db.models import Sum
from drf_spectacular.utils import extend_schema_field, OpenApiTypes
from decimal import Decimal
from django.utils import timezone
from django.db import transaction
from drf_spectacular.utils import inline_serializer

User = get_user_model()

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    remember_me = serializers.BooleanField(default=False, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['login_field'] = serializers.CharField(required=True)
        self.fields.pop(self.username_field, None)

    def validate(self, attrs):
        login_field = attrs['login_field']
        password = attrs['password']
        remember_me = attrs['remember_me']

        # Find user by email or phone (momo_number)
        try:
            if '@' in login_field:
                user = User.objects.select_related('profile').get(email=login_field)
            else:
                try:
                    parsed = phonenumbers.parse(login_field, "GH")
                    if phonenumbers.is_valid_number(parsed):
                        normalized_phone = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
                    else:
                        normalized_phone = login_field.strip()
                except Exception:
                    normalized_phone = login_field.strip()

                salt = getattr(settings, "HASH_SALT", settings.SECRET_KEY)
                hash_input = f"{normalized_phone}{salt}".encode('utf-8')
                phone_hash = hashlib.sha256(hash_input).hexdigest()

                # Lookup by Hash
                user = User.objects.select_related('profile').get(profile__momo_number_hash=phone_hash)

        except (User.DoesNotExist, Profile.DoesNotExist):
            raise AuthenticationFailed('No user found with this email or phone')

        if not user.check_password(password):
            raise AuthenticationFailed('Incorrect password')

        if not user.is_verified:
            raise AuthenticationFailed('Account not verified. Please verify your phone first.')

        # Generate tokens
        refresh = RefreshToken.for_user(user)

        # Extend refresh lifetime if remember_me is True
        if remember_me:
            extended_lifetime = settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'] * 30
            refresh.set_exp(lifetime=extended_lifetime)

        return {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }

class FullSignupSerializer(serializers.Serializer):
    """
    Serializer used solely for documenting the FullSignupView request body.
    The validation and creation logic remains in the view.
    """
    email = serializers.EmailField(
        max_length=255,
        help_text="User's email address."
    )
    password = serializers.CharField(
        write_only=True,
        min_length=8,
        help_text="Desired password."
    )
    password2 = serializers.CharField(
        write_only=True,
        help_text="Password confirmation."
    )
    full_name = serializers.CharField(
        max_length=255,
        help_text="Full name of the user."
    )
    date_of_birth = serializers.DateField(
        help_text="Date of birth (YYYY-MM-DD)."
    )
    user_type = serializers.ChoiceField(
        choices=[('individual', 'Individual'), ('business', 'Business')],
        help_text="Type of user: 'individual' or 'business'."
    )
    ghana_post_address = serializers.CharField(
        max_length=255,
        help_text="User's Ghana Post Digital Address."
    )
    momo_provider = serializers.CharField(
        max_length=50,
        help_text="Mobile Money provider (e.g., MTN, Vodafone)."
    )
    momo_number = serializers.CharField(
        max_length=15,
        help_text="Mobile Money phone number."
    )
    momo_name = serializers.CharField(
        max_length=255,
        help_text="Name registered on the Mobile Money account."
    )
    profile_picture = serializers.FileField(
        required=False,
        allow_null=True,
        help_text="Optional: User's profile photo (e.g., a selfie)."
    )

class SignupSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    password2 = serializers.CharField(write_only=True)
    class Meta:
        model = User
        fields = ('email', 'password', 'password2')
    def validate(self, data):
        if data['password'] != data['password2']:
            raise serializers.ValidationError("Passwords don't match")
        return data
    def create(self, validated_data):
        validated_data.pop('password2')
        user = User.objects.create_user(
            email=validated_data['email'],
            password=validated_data['password'],
            username=validated_data['email'].split('@')[0]
        )
        return user
class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = [
            'id', 'full_name', 'date_of_birth', 'user_type',
            'ghana_post_address', 'profile_picture',
            'momo_provider', 'momo_number', 'momo_name'
        ]

        read_only_fields = ('id', 'user')

    def validate_full_name(self, value):
        return html.escape(value.strip())

    def validate_ghana_post_address(self, value):
        return value.strip().upper()

    def validate_momo_number(self, value):
        stripped_value = value.strip()
        if len(stripped_value) < 9:
            raise serializers.ValidationError("Mobile money number is too short.")
        return stripped_value


class SendOTPSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=20)
class VerifyOTPSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=20)
    code = serializers.CharField(max_length=10)

class ForgotPasswordSerializer(serializers.Serializer):
    login_field = serializers.CharField(required=True)
class ResetPasswordSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20, required=True)
    code = serializers.CharField(max_length=10, required=True)
    password = serializers.CharField(min_length=8, required=True)
    password2 = serializers.CharField(required=True)

    def validate(self, data):
        if data['password'] != data['password2']:
            raise ValidationError("Passwords don't match")
        return data

class GroupAdminKYCSerializer(serializers.ModelSerializer):
    ghana_card_front = serializers.ImageField(
        required=True,
        write_only=True,
        help_text="Upload Ghana Card front"
    )
    ghana_card_back = serializers.ImageField(
        required=True,
        write_only=True,
        help_text="Upload Ghana Card back"
    )
    live_photo = serializers.ImageField(
        required=True,
        write_only=True,
        help_text="Upload live selfie"
    )


    ghana_card_front_url = serializers.ReadOnlyField(source='ghana_card_front_signed_url')
    ghana_card_back_url = serializers.ReadOnlyField(source='ghana_card_back_signed_url')
    live_photo_url = serializers.ReadOnlyField(source='live_photo_signed_url')

    class Meta:
        model = GroupAdminKYC
        fields = [
            'ghana_card_front', 'ghana_card_back', 'live_photo',
            'ghana_card_front_url', 'ghana_card_back_url', 'live_photo_url',
            'is_manually_verified', 'verified_at'
        ]
        read_only_fields = ['is_manually_verified', 'verified_at']

    def _validate_file(self, value):
        """Shared logic to prevent 'Image Bomb' attacks and invalid formats."""

        limit = 5 * 1024 * 1024
        if value.size > limit:
            raise serializers.ValidationError("File size too large. Maximum allowed size is 5MB.")

        ext = os.path.splitext(value.name)[1].lower()
        if ext not in ['.jpg', '.jpeg', '.png']:
            raise serializers.ValidationError("Unsupported file format. Please upload a JPG or PNG.")

        return value

    def validate_ghana_card_front(self, value):
        return self._validate_file(value)

    def validate_ghana_card_back(self, value):
        return self._validate_file(value)

    def validate_live_photo(self, value):
        return self._validate_file(value)

    def create(self, validated_data):
        user = self.context['request'].user
        return GroupAdminKYC.objects.create(user=user, **validated_data)
class SavingsGroupCreateSerializer(serializers.ModelSerializer):
    kyc = GroupAdminKYCSerializer(required=True)

    class Meta:
        model = SavingsGroup
        fields = [
            'group_name',
            'contribution_amount',
            'frequency',
            'payout_timeline_days',
            'expected_members',
            'description',
            'kyc'
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'request' in self.context and self.context['request'].method == 'POST':
            user = self.context['request'].user
            self.kyc_exists = GroupAdminKYC.objects.filter(user=user).exists()
            if self.kyc_exists:
                self.fields['kyc'].required = False
                kyc_fields = self.fields['kyc'].fields
                for field_name in kyc_fields:
                    kyc_fields[field_name].required = False


    def validate_group_name(self, value):
        return html.escape(value.strip())

    def validate_description(self, value):
        if value:
            return html.escape(value.strip())
        return value

    def validate_contribution_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Contribution amount must be a positive value.")
        if value > 50000:
            raise serializers.ValidationError("Contribution amount exceeds maximum allowed limit.")
        return value

    def validate_expected_members(self, value):
        if value < 2:
            raise serializers.ValidationError("A savings group must have at least 2 members.")
        if value > 50:
            raise serializers.ValidationError("Maximum group size is 50 members.")
        return value

    def validate_payout_timeline_days(self, value):
        if value < 1:
            raise serializers.ValidationError("Payout timeline must be at least 1 day.")
        return value


    @transaction.atomic
    def create(self, validated_data):

        kyc_data = validated_data.pop('kyc', None)
        user = self.context['request'].user

        # Handle KYC
        kyc_exists = getattr(self, 'kyc_exists', GroupAdminKYC.objects.filter(user=user).exists())
        if not kyc_exists and kyc_data:
            GroupAdminKYC.objects.create(
                user=user,
                ghana_card_front=kyc_data.get('ghana_card_front'),
                ghana_card_back=kyc_data.get('ghana_card_back'),
                live_photo=kyc_data.get('live_photo')
            )

        # # Create the Savings Group
        group = SavingsGroup.objects.create(
            status='pending',
            **validated_data
        )

        # Add the admin as the first member
        GroupMembership.objects.create(user=user, group=group)

        return group

class SavingsGroupSerializer(serializers.ModelSerializer):
    admin_name = serializers.CharField(source='admin.profile.full_name', read_only=True)
    admin_phone = serializers.CharField(source='admin.profile.momo_number', read_only=True)
    admin_photo = serializers.URLField(source='admin.profile.profile_picture', read_only=True, allow_null=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    total_group_savings = serializers.SerializerMethodField()

    total_savings = serializers.SerializerMethodField(
        help_text="Total amount the current user has personally contributed to this group."
    )
    next_due = serializers.SerializerMethodField(
        help_text="Next payout date (end of current contribution cycle)."
    )

    class Meta:
        model = SavingsGroup
        fields = [
            'id', 'group_name', 'contribution_amount', 'frequency',
            'payout_timeline_days', 'expected_members', 'current_members',
            'description', 'status', 'status_display', 'created_at',
            'admin_name', 'admin_phone', 'admin_photo',
            'total_savings', 'total_group_savings', 'next_due', 'public_id'
        ]
        read_only_fields = ['status', 'current_members', 'created_at']

    @extend_schema_field(OpenApiTypes.DECIMAL)
    def get_total_savings(self, obj):
        """
        Returns the users total personal contributions.
        """
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return Decimal('0.00')

        try:
            membership = obj.memberships.get(user=request.user)
            total = membership.contributions.filter(is_verified=True).aggregate(
                total=Sum('amount')
            )['total']
            return total or Decimal('0.00')
        except GroupMembership.DoesNotExist:
            return Decimal('0.00')

    @extend_schema_field(OpenApiTypes.DECIMAL)
    def get_total_group_savings(self, obj):
        """
        Calculates the total amount contributed by ALL members in the group.
        """
        # Summing contributions from all memberships tied to this group
        total = Contribution.objects.filter(
            membership__group=obj,
            is_verified=True
        ).aggregate(total=Sum('amount'))['total']

        return total or Decimal('0.00')

    @extend_schema_field(OpenApiTypes.STR)
    def get_next_due(self, obj):
        if obj.next_payout_date:
            return obj.next_payout_date.strftime('%Y-%m-%d')
        return None

class RequestingUserSerializer(serializers.ModelSerializer):
    """Minimal serializer to show details of the user who submitted the request."""
    full_name = serializers.CharField(source='profile.full_name', read_only=True)
    momo_number = serializers.CharField(source='profile.momo_number', read_only=True)
    # NEW: profile picture (Cloudinary URL) – exactly the same field used everywhere else
    profile_picture = serializers.URLField(
        source='profile.profile_picture',
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = User
        fields = ['id', 'email', 'full_name', 'momo_number', 'profile_picture']

class GroupJoinRequestSerializer(serializers.ModelSerializer):
    """Serializer for admin to view pending join requests."""
    user_details = RequestingUserSerializer(source='user', read_only=True)
    group_name = serializers.CharField(source='group.group_name', read_only=True)

    reason = serializers.CharField(
        allow_blank=True,
        allow_null=True,
        required=False,
        default='',
        help_text="Reason provided by the user when requesting to join."
    )

    class Meta:
        model = GroupJoinRequest
        fields = [
            'id',
            'group',
            'group_name',
            'user',
            'user_details',
            'status',
            'requested_at',
            'reason'
        ]
        read_only_fields = ['group', 'user', 'status', 'requested_at', 'handled_by', 'handled_at']

class GroupJoinActionSerializer(serializers.Serializer):
    """Serializer for the admin to take an action (Approve/Reject)."""
    action = serializers.ChoiceField(
        choices=['approve', 'reject'],
        help_text="Action to take on the request: 'approve' or 'reject'."
    )

class GroupJoinRequestCreateSerializer(serializers.Serializer):
    """
    Serializer for submitting a join request to a savings group.
    Only 'reason' is accepted from the user input.
    """
    reason = serializers.CharField(
        max_length=1000,
        allow_blank=True,
        required=False,
        trim_whitespace=True,
        help_text="Optional explanation of why you want to join this group. This will be visible to the group admin."
    )

    def validate_reason(self, value):
        if value and len(value.strip()) < 10:
            raise serializers.ValidationError("Reason should be at least 10 characters if provided.")
        return value.strip()


class GroupDashboardCardSerializer(serializers.ModelSerializer):
    group_name = serializers.CharField(read_only=True)
    current_members = serializers.IntegerField(read_only=True)
    next_payout_days = serializers.SerializerMethodField()
    user_total_contribution = serializers.SerializerMethodField()
    total_saved = serializers.SerializerMethodField()
    progress_percentage = serializers.SerializerMethodField()

    class Meta:
        model = SavingsGroup
        fields = [
            'id', 'group_name', 'current_members', 'next_payout_days',
            'user_total_contribution', 'total_saved', 'progress_percentage',
            'contribution_amount', 'frequency'
        ]

    def _get_prefetched_contributions(self, obj):
        """
        Helper to get contributions from pre-loaded memory.
        This avoids N+1 queries by accessing the prefetched 'memberships'
        and their 'contributions'.
        """

        all_contributions = []
        for membership in obj.memberships.all():
            all_contributions.extend(list(membership.contributions.all()))
        return all_contributions

    @extend_schema_field(OpenApiTypes.INT)
    def get_next_payout_days(self, obj):
        return obj.days_until_next_payout

    @extend_schema_field(OpenApiTypes.DECIMAL)
    def get_user_total_contribution(self, obj):
        """
        Calculates personal verified contributions using in-memory data.
        """
        user = self.context['request'].user
        if not user.is_authenticated:
            return Decimal('0.00')

        contribs = self._get_prefetched_contributions(obj)

        total = sum(
            c.amount for c in contribs
            if c.membership.user_id == user.id and c.is_verified
        )

        return Decimal(total).quantize(Decimal("0.01"))

    @extend_schema_field(OpenApiTypes.DECIMAL)
    def get_total_saved(self, obj):
        """
        Total verified contributions for the ENTIRE group in the CURRENT cycle using in-memory data.
        """
        current_cycle = obj.current_cycle_number
        contribs = self._get_prefetched_contributions(obj)

        total = sum(
            c.amount for c in contribs
            if c.cycle_number == current_cycle and c.is_verified
        )

        return Decimal(total).quantize(Decimal("0.01"))

    @extend_schema_field(OpenApiTypes.FLOAT)
    def get_progress_percentage(self, obj):
        """
        Visual progress of the current cycle's funding using in-memory data.
        """
        current_cycle = obj.current_cycle_number
        contribs = self._get_prefetched_contributions(obj)

        total_contributed = sum(
            c.amount for c in contribs
            if c.cycle_number == current_cycle and c.is_verified
        )

        expected_per_cycle = obj.contribution_amount * obj.expected_members

        if expected_per_cycle == 0:
            return 0.0

        percentage = (Decimal(total_contributed) / expected_per_cycle) * Decimal("100.0")
        return float(percentage.quantize(Decimal("0.1")))



class DashboardResponseSerializer(serializers.Serializer):
    """
    Serializer defining the complete structure of the /api/accounts/dashboard/ GET response.
    """
    total_savings = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text='Total amount saved by the user across all groups.'
    )
    growth_percentage = serializers.FloatField(
        help_text='Percentage change in total savings compared to the previous month.'
    )
    growth_text = serializers.CharField(
        help_text="A display string showing the growth (e.g., '+12.5% from last month')."
    )
    joined_groups = GroupDashboardCardSerializer(
        many=True,
        help_text='List of active groups the authenticated user is a member of.'
    )

class SavingsGoalCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for creating a new personal savings goal.
    """
    class Meta:
        model = SavingsGoal
        fields = [
            'name', 'target_amount', 'regular_contribution', 'target_date','frequency']

    def validate(self, data):
        """
        Additional validation: ensure target date is in the future.
        """
        if data['target_date'] < timezone.now().date():
            raise serializers.ValidationError({"target_date": "Target date must be in the future."})

        if data['target_amount'] <= 0:
            raise serializers.ValidationError({"target_amount": "Target amount must be greater than zero."})

        if data['regular_contribution'] <= 0:
             raise serializers.ValidationError({"regular_contribution": "Regular contribution must be greater than zero."})

        return data

    def create(self, validated_data):
        user = self.context['request'].user
        goal = SavingsGoal.objects.create(user=user, **validated_data)
        return goal

class SavingsGoalSerializer(serializers.ModelSerializer):
    """
    Base serializer for SavingsGoal with high-precision Decimal fields.
    """
    current_saved = serializers.SerializerMethodField()
    progress_percentage = serializers.SerializerMethodField()
    days_left = serializers.SerializerMethodField()
    contribution_display = serializers.SerializerMethodField()

    class Meta:
        model = SavingsGoal
        fields = [
            'id', 'name', 'target_amount', 'regular_contribution', 'frequency',
            'target_date', 'created_at', 'current_saved', 'progress_percentage',
            'days_left', 'contribution_display'
        ]
        read_only_fields = ['created_at', 'current_saved', 'progress_percentage', 'days_left']

    @extend_schema_field(OpenApiTypes.DECIMAL)
    def get_current_saved(self, obj):
        """
        Returns total confirmed savings for this goal as a Decimal.
        """
        total = obj.contributions.filter(is_verified=True).aggregate(
            total=Sum('amount')
        )['total']
        return total or Decimal('0.00')

    @extend_schema_field(OpenApiTypes.FLOAT)
    def get_progress_percentage(self, obj):
        """
        Returns the percentage toward the goal. Float is used for UI progress bars.
        """
        current = self.get_current_saved(obj)
        target = obj.target_amount

        if target <= 0:
            return 0.0

        percentage = (current / target) * 100
        return float(round(percentage, 1))

    @extend_schema_field(OpenApiTypes.STR)
    def get_days_left(self, obj):
        return obj.days_left

    @extend_schema_field(OpenApiTypes.STR)
    def get_contribution_display(self, obj):
        freq_map = dict(SavingsGoal.FREQUENCY_CHOICES)
        frequency_label = freq_map.get(obj.frequency, obj.frequency).capitalize()
        return f"₵{obj.regular_contribution} {frequency_label}"

class GoalDashboardCardSerializer(SavingsGoalSerializer):
    """
    Serializer used specifically for the individual goals.
    It inherits all fields and methods from SavingsGoalSerializer, providing:
      - goal name
      - current_saved of target_amount (e.g., ₵3,200 of ₵5,000)
      - contribution_display (e.g., ₵500 Monthly)
      - progress_percentage (for progress bar)
      - days_left (e.g., "6 days" or "Overdue")
    """
    pass

class GoalsDashboardResponseSerializer(serializers.Serializer):
    """
    Top-level response serializer for the /goals/dashboard/ endpoint.
    """
    total_target = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Sum of target_amount across all user's goals."
    )
    total_saved = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Sum of confirmed current_saved across all user's goals."
    )
    overall_progress = serializers.FloatField(
        help_text="Overall progress percentage (total_saved / total_target × 100)."
    )
    active_goals_count = serializers.IntegerField(
        help_text="Number of goals that are still active."
    )
    goals = GoalDashboardCardSerializer(
        many=True,
        help_text="Detailed list of all the user's savings goals as cards."
    )


class SavingsGoalUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer for PATCH (partial) updates to an existing SavingsGoal.
    Enhanced with precise error messaging and future-proof validation
    """
    class Meta:
        model = SavingsGoal
        fields = [
            'name',
            'target_amount',
            'regular_contribution',
            'target_date',
            'frequency'
        ]
        partial = True

    def validate(self, data):
        """
        All business rule validations in one place.
        Uses the model's @property current_saved for accuracy.
        """
        instance = self.instance

        # Future target date
        if 'target_date' in data and data['target_date'] < timezone.now().date():
            raise serializers.ValidationError({
                "target_date": "Target date must be in the future."
            })

        # Positive amounts
        if 'target_amount' in data and data['target_amount'] <= 0:
            raise serializers.ValidationError({
                "target_amount": "Target amount must be greater than zero."
            })

        if 'regular_contribution' in data and data['regular_contribution'] <= 0:
            raise serializers.ValidationError({
                "regular_contribution": "Regular contribution must be greater than zero."
            })

        # Cannot reduce target below already saved amount
        if 'target_amount' in data and instance and data['target_amount'] < instance.current_saved:
            raise serializers.ValidationError({
                "target_amount": (
                    f"Cannot reduce target below already saved amount. "
                    f"Current saved: ₵{instance.current_saved:,.2f} | "
                    f"You tried to set: ₵{data['target_amount']:,.2f}"
                )
            })

        # frequency must be valid
        if 'frequency' in data and data['frequency'] not in dict(SavingsGoal.FREQUENCY_CHOICES):
            raise serializers.ValidationError({
                "frequency": "Invalid frequency. Choose from daily, weekly, or monthly."
            })

        return data

class GroupsStatsResponseSerializer(serializers.Serializer):
    """
    Serializer for the /groups/stats/ endpoint.
    Ensures group-wide savings totals are handled with high precision.
    """
    total_groups = serializers.IntegerField(
        help_text="Number of active groups the user is currently a member of."
    )
    total_members = serializers.IntegerField(
        help_text="Total number of members across all the user's active groups."
    )
    group_savings = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Combined total of confirmed savings across all the user's active groups."
    )

class JoinRequestsStatsSerializer(serializers.Serializer):
    pending = serializers.IntegerField(
        help_text="Number of pending join requests across all groups administered by the user."
    )
    accepted = serializers.IntegerField(
        help_text="Number of accepted join requests across all groups administered by the user."
    )
    declined = serializers.IntegerField(
        help_text="Number of declined (rejected) join requests across all groups administered by the user."
    )


class AnalyticsStatsSerializer(serializers.Serializer):
    total_savings = serializers.DecimalField(max_digits=12, decimal_places=2)
    monthly_growth = serializers.DecimalField(max_digits=12, decimal_places=2)
    monthly_growth_percentage = serializers.FloatField()
    active_groups = serializers.IntegerField()
    goals_progress = serializers.FloatField()

class SavingsOverTimeItem(serializers.Serializer):
    month = serializers.CharField()
    amount = serializers.FloatField()
    contributions = serializers.FloatField()

class SavingsDistributionItem(serializers.Serializer):
    name = serializers.CharField()
    value = serializers.FloatField()

class GroupPerformanceItem(serializers.Serializer):
    name = serializers.CharField()
    savings = serializers.FloatField()
    members = serializers.IntegerField()

class AnalyticsResponseSerializer(serializers.Serializer):
    """
    Top-level response serializer for the /accounts/analytics/ endpoint.
    Used both for runtime validation and Swagger documentation.
    """
    stats = AnalyticsStatsSerializer()
    savings_over_time = SavingsOverTimeItem(many=True)
    savings_distribution = SavingsDistributionItem(many=True)
    group_performance = GroupPerformanceItem(many=True)

    key_insights = serializers.ListField(
        child=inline_serializer(
            name='KeyInsight',
            fields={
                'title': serializers.CharField(),
                'description': serializers.CharField(),
            }
        ),
        help_text="List of 3 dynamic key insights based on user activity"
    )

    recommendations = serializers.ListField(
        child=inline_serializer(
            name='Recommendation',
            fields={
                'title': serializers.CharField(),
                'description': serializers.CharField(),
            }
        ),
        help_text="List of 3 personalized recommendations"
    )

class ChangePasswordSerializer(serializers.Serializer):
    """Secure password change for authenticated users."""
    current_password = serializers.CharField(
        write_only=True,
        required=True,
        help_text="Your current password (required for security)"
    )
    new_password = serializers.CharField(
        write_only=True,
        min_length=8,
        required=True,
        help_text="New password (minimum 8 characters)"
    )
