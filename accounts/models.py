import os
import uuid
import secrets
from decimal import Decimal
from phonenumber_field.modelfields import PhoneNumberField
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from cloudinary.models import CloudinaryField
from django.core.validators import MinValueValidator
from django.db.models import Sum
from dateutil.relativedelta import relativedelta
import datetime
from django.utils import timezone
from django.conf import settings
from encrypted_model_fields.fields import EncryptedCharField
import hashlib
import phonenumbers
import cloudinary.uploader
import cloudinary.utils
from django.db import IntegrityError, models, transaction
from django.db.models import CheckConstraint, Q

class SoftDeleteModel(models.Model):
    """Base class to prevent hard-deleting critical data."""
    is_active = models.BooleanField(default=True, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    def delete(self, *args, **kwargs):
        self.is_active = False
        self.deleted_at = timezone.now()
        self.save()

class User(AbstractUser):
    email = models.EmailField(unique=True, db_index=True)
    phone_number = PhoneNumberField(unique=True, blank=True, null=True, db_index=True)
    is_verified = models.BooleanField(default=False)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    groups = models.ManyToManyField(
        'auth.Group',
        related_name='custom_user_groups',
        blank=True,
        help_text='The groups this user belongs to.',
        verbose_name='groups',
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        related_name='custom_user_permissions',
        blank=True,
        help_text='Specific permissions for this user.',
        verbose_name='user permissions',
    )


class Profile(models.Model):
    USER_TYPE_CHOICES = (('student', 'Student'), ('worker', 'Worker'))
    MOMO_PROVIDER_CHOICES = (
        ('mtn', 'MTN MoMo'),
        ('telecel', 'Telecel Cash'),
        ('airteltigo', 'AirtelTigo Cash'),
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    full_name = models.CharField(max_length=255)
    date_of_birth = models.DateField()
    user_type = models.CharField(max_length=20, choices=USER_TYPE_CHOICES)
    ghana_post_address = models.CharField(
        max_length=20,
        validators=[RegexValidator(r'^[A-Z]{2}-\d{3}-\d{4}$', 'Format: GA-123-4567')]
    )
    profile_picture = models.URLField(max_length=1000, blank=True, null=True, verbose_name="Profile Picture URL")
    momo_provider = models.CharField(max_length=20, choices=MOMO_PROVIDER_CHOICES)
    momo_number = EncryptedCharField(max_length=255)
    momo_number_hash = models.CharField(max_length=128, unique=True, db_index=True, editable=False)
    momo_name = models.CharField(max_length=255)

    paystack_recipient_code = models.CharField(
    max_length=100,
    blank=True,
    null=True,
    db_index=True,
    help_text=(
        "Cached Paystack RCP_xxx code for this user's MoMo account. "
        "Created on first payout/cashout and reused thereafter."),
    )

    @staticmethod
    def normalize_momo_number(raw: str) -> str:
        """Always return E.164 format when valid, otherwise the stripped original."""
        if not raw:
            return ""
        try:
            parsed = phonenumbers.parse(raw, "GH")
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
            return raw.strip()
        except Exception:
            return raw.strip()

    @staticmethod
    def compute_momo_hash(normalized: str) -> str:
        """Compute the deterministic hash used for uniqueness."""
        if not normalized:
            return ""
        salt = settings.HASH_SALT
        hash_input = f"{normalized}{salt}".encode("utf-8")
        return hashlib.sha256(hash_input).hexdigest()

    def save(self, *args, **kwargs):
        if self.momo_number:
            normalized = Profile.normalize_momo_number(self.momo_number)
            self.momo_number = normalized
            self.momo_number_hash = Profile.compute_momo_hash(normalized)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.full_name


    def validate_image_extension(value):
        ext = os.path.splitext(value.name)[1].lower()
        valid_extensions = ['.jpg', '.jpeg', '.png']
        if ext not in valid_extensions:
            raise ValidationError('Only JPG, JPEG, and PNG files are allowed.')

class GroupAdminKYC(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='kyc')

    ghana_card_front = CloudinaryField(
        'image',
        folder='snappx/kyc/ghana_card/front',
        type='private',
        transformation=[{'quality': 'auto'}],
        null=True, blank=True
    )
    ghana_card_back = CloudinaryField(
        'image',
        folder='snappx/kyc/ghana_card/back',
        type='private',
        transformation=[{'quality': 'auto'}],
        null=True, blank=True
    )
    live_photo = CloudinaryField(
        'image',
        folder='snappx/kyc/live_photos',
        type='private',
        transformation=[{'width': 800, 'crop': 'limit'}],
        null=True, blank=True
    )

    is_manually_verified = models.BooleanField(default=False, db_index=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='verified_kycs')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"KYC for {self.user.email}"

    def _get_signed_url(self, field):
        """Generates a temporary signed URL for admin viewing."""
        if not field:
            return None

        url, options = cloudinary.utils.cloudinary_url(
            field.public_id,
            sign_url=True,
            type="private",
            secure=True,
            expires_at=int((timezone.now() + timezone.timedelta(minutes=30)).timestamp())
        )
        return url

    @property
    def ghana_card_front_signed_url(self):
        return self._get_signed_url(self.ghana_card_front)

    @property
    def ghana_card_back_signed_url(self):
        return self._get_signed_url(self.ghana_card_back)

    @property
    def live_photo_signed_url(self):
        return self._get_signed_url(self.live_photo)


    @transaction.atomic
    def approve(self, admin_user):
        """Strict atomic method for manual verification."""
        self.is_manually_verified = True
        self.verified_at = timezone.now()
        self.verified_by = admin_user
        self.save(update_fields=['is_manually_verified', 'verified_at', 'verified_by'])

        user = self.user
        if hasattr(user, 'is_verified'):
            user.is_verified = True
            user.save(update_fields=['is_verified'])


    def delete(self, *args, **kwargs):
        """Ensure physical images are deleted from Cloudinary when record is removed."""
        for field_name in ['ghana_card_front', 'ghana_card_back', 'live_photo']:
            image_field = getattr(self, field_name)
            if image_field:
                cloudinary.uploader.destroy(image_field.public_id, type="private")
        super().delete(*args, **kwargs)

class SavingsGroup(models.Model):
    FREQUENCY_CHOICES = (
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
    )
    STATUS_CHOICES = (
        ('pending', 'Pending Approval'),
        ('active', 'Active'),
        ('rejected', 'Rejected'),
        ('suspended', 'Suspended'),
    )

    start_date = models.DateField(
        null=True, blank=True,
        help_text="Date when the group officially starts contributing (set when activated)"
    )
    last_payout_processed_date = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Date when the daily payout orchestrator last processed this group. "
                  "Prevents duplicate dispatch if the orchestrator runs multiple times on the same day."
    )
    payout_interval_days = models.PositiveIntegerField(
        default=30,
        help_text="Computed from frequency: daily=1, weekly=7, monthly≈30"
    )
    current_cycle_number = models.PositiveIntegerField(
        default=1,
        help_text="The actual cycle the group is in. Incremented manually on payout."
    )

    @property
    def total_pot_per_cycle(self):
        return self.contribution_amount * self.expected_members

    @property
    def next_payout_date(self):
        if not self.start_date: return None
        return self.start_date + datetime.timedelta(days=self.current_cycle_number * self.payout_interval_days)

    @property
    def days_until_next_payout(self):
        target = self.next_payout_date
        if not target:
            return None
        today = timezone.now().date()
        diff = (target - today).days
        return max(0, diff)

    @property
    def current_cycle_end_date(self):
        """Optional: The deadline for contributions for the current cycle"""
        return self.next_payout_date

    admin = models.ForeignKey(User, on_delete=models.PROTECT, related_name='admin_of_groups')
    group_name = models.CharField(max_length=255, unique=True)
    contribution_amount = models.DecimalField(max_digits=12, decimal_places=2)
    frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES)
    payout_timeline_days = models.PositiveIntegerField(help_text="e.g., 30 days")
    expected_members = models.PositiveIntegerField()
    current_members = models.PositiveIntegerField(default=1)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)
    approved_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='approved_groups'
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    public_id = models.CharField(max_length=20, unique=True, editable=False, default=None, null=True)
    is_public = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        # Derive payout_interval_days from frequency
        interval_map = {'daily': 1, 'weekly': 7, 'monthly': 30}
        self.payout_interval_days = interval_map.get(self.frequency, 30)

        # Generate public_id on first save with collision-resistant retry
        if not self.public_id:
            max_attempts = 10
            for attempt in range(max_attempts):
                candidate = str(
                    secrets.randbelow(10**19 - 10**18) + 10**18
                )
                self.public_id = candidate
                try:
                    super().save(*args, **kwargs)
                    return
                except IntegrityError as e:
                    if 'public_id' not in str(e).lower():
                        raise
                    self.public_id = None
                    if attempt == max_attempts - 1:
                        raise IntegrityError(
                            f"Could not generate a unique public_id after {max_attempts} attempts."
                        )
                    continue

        super().save(*args, **kwargs)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.group_name} by {self.admin.profile.full_name}"

    def clean(self):
        if self.current_members > self.expected_members:
            raise ValidationError("Current members cannot exceed expected members.")


class GroupInviteLink(models.Model):
    """
    One persistent, revokable invite token per group.
    Admin can regenerate the token to invalidate old links.
    Click-count is incremented atomically on every preview fetch.
    """
    group = models.OneToOneField(
        'SavingsGroup',
        on_delete=models.CASCADE,
        related_name='invite_link',
    )
    token = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        editable=False,
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_invite_links',
    )
    is_active    = models.BooleanField(default=True, db_index=True)
    click_count  = models.PositiveIntegerField(default=0)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Group Invite Link"
        verbose_name_plural = "Group Invite Links"

    def save(self, *args, **kwargs):
        if not self.token:
            import secrets as _secrets
            self.token = _secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def __str__(self):
        flag = "active" if self.is_active else "revoked"
        return f"Invite for {self.group.group_name} [{flag}]"


class GroupJoinRequest(models.Model):
    """
    Tracks a user's request to join a SavingsGroup, awaiting admin approval.
    """
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled')
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='join_requests',
        help_text="The user requesting to join."
    )
    group = models.ForeignKey(
        'SavingsGroup',
        on_delete=models.CASCADE,
        related_name='join_requests',
        help_text="The group the user is requesting to join."
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        db_index=True
    )
    reason = models.TextField(
        blank=True,
        null=True,
        help_text="Optional reason provided by the user for wanting to join the group."
    )

    requested_at = models.DateTimeField(auto_now_add=True)

    # Fields for Admin action
    handled_by = models.ForeignKey(
        User,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='handled_join_requests',
        help_text="The admin who handled this request."
    )
    handled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('user', 'group')
        verbose_name = "Group Join Request"
        verbose_name_plural = "Group Join Requests"
        ordering = ['-requested_at']

    def __str__(self):
        return f"Request by {self.user.email} for {self.group.group_name} ({self.status})"

class GroupMembership(models.Model):
    """
    Represents an active, approved member of a SavingsGroup.
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='memberships',
        help_text="The active member."
    )
    group = models.ForeignKey(
        'SavingsGroup',
        on_delete=models.CASCADE,
        related_name='memberships',
        help_text="The group the user is a member of."
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'group')
        verbose_name = "Group Membership"
        verbose_name_plural = "Group Memberships"

    def __str__(self):
        return f"{self.user.email} is a member of {self.group.group_name}"

class PayoutOrder(models.Model):
    """Defines the rotational payout order. Admin sets this after group is full/active."""
    group = models.ForeignKey(
        'SavingsGroup',
        on_delete=models.CASCADE,
        related_name='payout_orders'
    )
    membership = models.ForeignKey(
        'GroupMembership',
        on_delete=models.CASCADE
    )
    position = models.PositiveIntegerField(
        help_text="1 = first to receive payout, 2 = second, etc."
    )
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['group', 'membership'],
                name='unique_payout_group_membership'
            ),
            models.UniqueConstraint(
                fields=['group', 'position'],
                name='unique_payout_group_position'
            ),
        ]
        ordering = ['position']

class Contribution(models.Model):
    """Record of a specific group payment."""
    membership = models.ForeignKey(GroupMembership, on_delete=models.PROTECT, related_name='contributions')
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    cycle_number = models.PositiveIntegerField()

    paid_at = models.DateTimeField(
        default=timezone.now,
        editable=True,
        db_index=True
    )

    is_verified = models.BooleanField(default=False)

    class Meta:
        unique_together = ('membership', 'cycle_number')
        ordering = ['-paid_at']


class SavingsGoal(models.Model):
    FREQUENCY_CHOICES = (
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='savings_goals')
    name = models.CharField(max_length=255)
    target_amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    regular_contribution = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    current_saved_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00')
    )
    target_date = models.DateField()
    frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES)
    last_contribution_date = models.DateField(null=True, blank=True)
    last_reminded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} for {self.user.email}"

    def clean(self):
        if self.target_date and self.target_date < timezone.now().date():
            raise ValidationError("Target date must be in the future.")
        if self.target_amount <= 0:
            raise ValidationError("Target amount must be positive.")

    @property
    def current_saved(self):
        """
        Calculates only VERIFIED savings.
        """
        total = self.contributions.filter(is_verified=True).aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0.00')
        return total

    @property
    def progress_percentage(self):
        if self.target_amount == 0:
            return Decimal('0.00')
        percentage = (self.current_saved / self.target_amount) * 100
        return percentage.quantize(Decimal('0.01'))

    @property
    def days_left(self):
        today = timezone.now().date()
        delta = self.target_date - today
        if delta.days < 0:
            return "Overdue"
        elif delta.days == 0:
            return "Due today"
        else:
            return f"{delta.days} days"

    @property
    def is_due(self):
        if not self.last_contribution_date:
            return True

        today = timezone.now().date()
        last_date = self.last_contribution_date
        delta_days = (today - last_date).days

        if self.frequency == 'daily':
            return delta_days >= 1
        elif self.frequency == 'weekly':
            return delta_days >= 7
        elif self.frequency == 'monthly':
            next_due = last_date + relativedelta(months=1)
            return today >= next_due
        return False

    @property
    def is_active(self):
        """
        A goal is active if it hasn't reached its target and hasn't expired.
        Uses verified savings to check completion.
        """
        return self.current_saved < self.target_amount and self.target_date >= timezone.now().date()

class GoalContribution(models.Model):
    goal = models.ForeignKey(SavingsGoal, on_delete=models.CASCADE, related_name='contributions')
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    paid_at = models.DateTimeField(
        default=timezone.now,
        editable=True,
        db_index=True
    )

    is_verified = models.BooleanField(default=False)

    class Meta:
        ordering = ['-paid_at']

class IdempotencyKey(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )
    key = models.CharField(max_length=255)

    request_hash = models.CharField(
        max_length=64,
        help_text="SHA-256 hash of the request body to ensure integrity.",
        db_index=True,
    )

    response_code = models.IntegerField()
    response_body = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'key')
        verbose_name = "Idempotency Key"
        verbose_name_plural = "Idempotency Keys"
        indexes = [
            models.Index(fields=['user', 'key']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        user_label = self.user.email if self.user_id else "anonymous"
        return f"{user_label} - {self.key}"


class Wallet(models.Model):
    """
    Acts as an internal account.
    Can belong to a human User (Personal Wallet)
    or a SavingsGroup (Group Pot).
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='wallet',
        null=True, blank=True
    )
    group = models.OneToOneField(
        'SavingsGroup',
        on_delete=models.PROTECT,
        related_name='group_wallet',
        null=True, blank=True
    )

    currency = models.CharField(max_length=3, default='GHS')
    current_balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            CheckConstraint(
                condition=Q(current_balance__gte=0),
                name="wallet_balance_not_negative",
                violation_error_message="Wallet balance cannot drop below zero."
            ),
            CheckConstraint(
                condition=(
                    Q(user__isnull=False, group__isnull=True) |
                    Q(user__isnull=True, group__isnull=False)
                ),
                name="wallet_must_have_one_owner",
                violation_error_message="Wallet must belong to exactly one User or Group."
            )
        ]

    def clean(self):
        # Integrity Check: A wallet cannot belong to both or neither
        if not self.user and not self.group:
            raise ValidationError("Wallet must be linked to either a User or a Group.")
        if self.user and self.group:
            raise ValidationError("Wallet cannot be linked to both a User and a Group.")

    def __str__(self):
        owner = self.user.email if self.user else f"Group: {self.group.group_name}"
        return f"{owner}'s Wallet ({self.currency} {self.current_balance})"


class LedgerEntryQuerySet(models.QuerySet):
    """
    Blocks ALL bulk deletion attempts on ledger entries.
    This catches:
      - LedgerEntry.objects.filter(...).delete()
      - Django Admin bulk delete actions
      - Any queryset-based delete()
    """
    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Ledger entries cannot be bulk deleted. "
            "Create a correcting entry instead."
        )


class ImmutableLedgerManager(models.Manager):
    """All queries on LedgerEntry go through this protected queryset."""
    def get_queryset(self):
        return LedgerEntryQuerySet(self.model, using=self._db)


class LedgerEntry(models.Model):
    """
    The Single Source of Truth.
    Records every movement of value. Immutable.
    """
    TRANSACTION_TYPES = (
        ('deposit', 'Deposit'),
        ('withdrawal', 'Withdrawal'),
        ('contribution', 'Group Contribution'),
        ('goal_contribution', 'Goal Contribution'),
        ('payout', 'Group Payout'),
        ('refund', 'Refund'),
    )
    DIRECTION_CHOICES = (
        ('credit', 'Credit (+, Money In)'),
        ('debit', 'Debit (-, Money Out)'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.PROTECT,
        related_name='ledger_entries'
    )
    actor = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='performed_transactions',
        null=True,
        blank=True
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    transaction_type = models.CharField(max_length=50, choices=TRANSACTION_TYPES)
    transaction_group_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Links related entries for double-entry integrity. Null for external inflows/outflows."
    )
    description = models.CharField(max_length=255)
    reference = models.CharField(max_length=255, unique=True, help_text="External ID or Internal UUID")
    request_id = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    related_group = models.ForeignKey(
        'SavingsGroup', null=True, blank=True, on_delete=models.SET_NULL
    )
    related_goal = models.ForeignKey(
        'SavingsGoal', null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = ImmutableLedgerManager()

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['wallet', 'created_at']),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError(
                "Ledger entries are immutable and cannot be updated. "
                "Create a correcting entry instead."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Ledger entries cannot be deleted. "
            "Create a correcting entry instead."
        )

    def __str__(self):
        sign = "+" if self.direction == 'credit' else "-"
        return f"{sign}{self.amount} ({self.transaction_type}) - {self.description}"

class Notification(models.Model):
    """
    Central notification store. Every in-app notification lives here.
    Designed for high-read, occasional-write workloads.
    Uses bulk_create for multi-user events (e.g. group activation).
    """

    NOTIFICATION_TYPES = [
        ('join_request',          'New Join Request'),
        ('join_approved',         'Join Request Approved'),
        ('join_rejected',         'Join Request Rejected'),
        ('payout_received',       'Payout Received'),
        ('contribution_reminder', 'Contribution Reminder'),
        ('goal_reminder',         'Goal Reminder'),
        ('group_activated',       'Group Activated'),
        ('new_member',            'New Member Joined'),
        ('goal_completed',        'Goal Completed'),
        ('system',                'System Notice'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
        db_index=True
    )
    notification_type = models.CharField(
        max_length=50,
        choices=NOTIFICATION_TYPES,
        db_index=True
    )
    title   = models.CharField(max_length=255)
    message = models.TextField()
    is_read = models.BooleanField(default=False, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at    = models.DateTimeField(null=True, blank=True)

    # Frontend page name to navigate to on click (e.g. 'Groups', 'Goals', 'Requests')
    action_url = models.CharField(max_length=100, blank=True, null=True)

    related_group = models.ForeignKey(
        'SavingsGroup', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='notifications'
    )
    related_goal = models.ForeignKey(
        'SavingsGoal', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='notifications'
    )

    # Arbitrary JSON for extra context (amounts, IDs, names)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_read', 'created_at']),
            models.Index(fields=['user', 'notification_type']),
        ]
        verbose_name        = 'Notification'
        verbose_name_plural = 'Notifications'

    def __str__(self):
        status = '●' if not self.is_read else '○'
        return f"{status} [{self.notification_type}] {self.title} → {self.user.email}"

    def mark_as_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=['is_read', 'read_at'])


class PaystackTransaction(models.Model):
    """
    Tracks every payment interaction with the Paystack API.

    This is separate from LedgerEntry:
    - LedgerEntry is our internal, immutable ledger (accounting truth).
    - PaystackTransaction tracks the Paystack-side lifecycle of a payment.

    One PaystackTransaction → one or more LedgerEntry rows (for transfers,
    the debit and credit are both in LedgerEntry, but only one PS tx exists).

    Transaction types:
    - topup:    User is adding money to their internal wallet (charge.success)
    - payout:   Group cycle payout going to a member (transfer.*)
    - cashout:  User withdrawing from their wallet to MoMo (transfer.*)
    """

    TRANSACTION_TYPES = (
        ("topup", "Wallet Top-Up"),
        ("payout", "Group Payout"),
        ("cashout", "User Cash-Out"),
    )

    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("success", "Success"),
        ("failed", "Failed"),
        ("reversed", "Reversed"),
    )

    # Who initiated this transaction
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="paystack_transactions",
        help_text="The user this transaction belongs to.",
    )

    # Our reference (what we send to Paystack)
    reference = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="Our unique reference sent to Paystack (snx-topup-*, snx-payout-*, snx-cashout-*).",
    )

    # Paystack's own reference/code (comes back in webhook / verify response)
    paystack_reference = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Paystack transaction ID (for charges) or transfer_code (for transfers).",
    )

    transaction_type = models.CharField(
        max_length=20,
        choices=TRANSACTION_TYPES,
        db_index=True,
    )

    # Amount in GHS (not pesewas — we store human-readable)
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Amount in GHS.",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        db_index=True,
    )

    # For payout: link to the group so we can update payout status
    related_group = models.ForeignKey(
        "SavingsGroup",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="paystack_transactions",
    )

    failure_reason = models.TextField(
        blank=True,
        null=True,
        help_text="Raw failure details from Paystack (transfer.failed data.failures).",
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Paystack Transaction"
        verbose_name_plural = "Paystack Transactions"
        indexes = [
            models.Index(fields=["user", "transaction_type", "status"]),
            models.Index(fields=["reference"]),
        ]

    def __str__(self):
        return (
            f"[{self.transaction_type.upper()}] {self.reference} "
            f"— ₵{self.amount} ({self.status})"
        )


# BOT INTEGRATION
class BotGroupLink(models.Model):
    """
    Records that a SavingsGroup has been linked to an external messaging bot.
    """

    PLATFORM_CHOICES = (
        ("whatsapp", "WhatsApp"),
        ("telegram", "Telegram"),
    )

    group = models.ForeignKey(
        "SavingsGroup",
        on_delete=models.CASCADE,
        related_name="bot_links",
    )
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES, db_index=True)
    linked_at = models.DateTimeField(auto_now_add=True)
    linked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bot_links_created",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    telegram_chat_id = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Telegram chat ID for the connected group chat. "
            "Set automatically when the bot receives /start in the group."
        ),
    )

    last_activity = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time the bot posted in this chat.",
    )

    class Meta:
        unique_together = ("group", "platform")
        verbose_name = "Bot Group Link"
        verbose_name_plural = "Bot Group Links"
        ordering = ["-linked_at"]

    def __str__(self):
        flag = "active" if self.is_active else "inactive"
        pending = " [pending /start]" if self.is_active and not self.telegram_chat_id and self.platform == "telegram" else ""
        return f"{self.group.group_name} ↔ {self.platform} [{flag}]{pending}"
