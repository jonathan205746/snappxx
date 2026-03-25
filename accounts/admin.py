from .models import GoalContribution, GroupAdminKYC, PayoutOrder, SavingsGoal, SavingsGroup, GroupJoinRequest, GroupMembership
from django.utils.html import format_html
from django.utils import timezone
from django.contrib import admin
from django.db import transaction
from django.utils.safestring import mark_safe

@admin.register(GroupAdminKYC)
class GroupAdminKYCAdmin(admin.ModelAdmin):
    list_display = ['user', 'verification_status', 'created_at']
    list_filter = ['is_manually_verified', 'created_at']
    search_fields = ['user__email', 'user__profile__momo_number']

    fieldsets = (
        ('User Info', {
            'fields': ('user', 'created_at')
        }),
        ('Verification Decision', {
            'fields': ('is_manually_verified', 'verified_by', 'verified_at'),
            'description': "Check the images below. If they match, check 'Is Manually Verified' and Save."
        }),
        ('Ghana Card (ID)', {
            'fields': ('front_preview', 'back_preview'),
            'description': "Verify the name and face on the ID card."
        }),
        ('Live Selfie', {
            'fields': ('live_preview',),
            'description': "Compare this selfie with the face on the Ghana Card above."
        }),
    )

    readonly_fields = [
        'user', 'created_at', 'verified_by', 'verified_at',
        'front_preview', 'back_preview', 'live_preview'
    ]

    def save_model(self, request, obj, form, change):
        if obj.is_manually_verified and not obj.verified_at:
            obj.approve(admin_user=request.user)
        else:
            super().save_model(request, obj, form, change)

    def front_preview(self, obj):
        url = obj.ghana_card_front_signed_url
        return self._image_tag(url)
    front_preview.short_description = "Ghana Card Front"

    def back_preview(self, obj):
        url = obj.ghana_card_back_signed_url
        return self._image_tag(url)
    back_preview.short_description = "Ghana Card Back"

    def live_preview(self, obj):
        url = obj.live_photo_signed_url
        return self._image_tag(url, color="#007bff")
    live_preview.short_description = "Live Selfie"

    def _image_tag(self, url, color="#ccc"):
        if not url:
            return "No Image Uploaded"
        return format_html(
            '<a href="{}" target="_blank">'
            '<img src="{}" width="400" style="border-radius:8px; border: 2px solid {};"/>'
            '</a>',
            url, url, color
        )

    def verification_status(self, obj):
        if obj.is_manually_verified:
            return mark_safe('<b style="color: green;">✅ Verified</b>')
        return mark_safe('<b style="color: red;">❌ Pending</b>')

@admin.register(SavingsGroup)
class SavingsGroupAdmin(admin.ModelAdmin):
    list_display = ['group_name', 'admin', 'contribution_amount', 'frequency', 'status', 'expected_members', 'created_at']
    list_filter = ['status', 'frequency', 'created_at']
    search_fields = ['group_name', 'admin__email']
    readonly_fields = ['admin', 'created_at', 'approved_at']
    actions = ['approve_groups', 'suspend_groups', 'reject_groups']

    @transaction.atomic
    def approve_groups(self, request, queryset):

        updated_count = 0
        for group in queryset:
            group.status = 'active'
            group.approved_by = request.user
            group.approved_at = timezone.now()

            if hasattr(group.admin, 'kyc'):
                group.admin.kyc.approve(admin_user=request.user)

            if group.current_members >= group.expected_members and not group.start_date:
                group.start_date = timezone.now().date()

                memberships = group.members.order_by('joined_at')
                for pos, membership in enumerate(memberships, start=1):
                    PayoutOrder.objects.get_or_create(
                        group=group,
                        membership=membership,
                        defaults={'position': pos}
                    )

            group.save()
            updated_count += 1

        self.message_user(request, f"Successfully activated {updated_count} groups and their admins.")

    approve_groups.short_description = "Approve and activate selected groups (if full)"

    def suspend_groups(self, request, queryset):
        queryset.update(status='suspended')
    suspend_groups.short_description = "Suspend selected groups"

    def reject_groups(self, request, queryset):
        queryset.update(status='rejected')
    reject_groups.short_description = "Reject selected groups"

@admin.register(GroupJoinRequest)
class GroupJoinRequestAdmin(admin.ModelAdmin):
    list_display = ['user', 'group', 'status', 'requested_at', 'handled_by']
    list_filter = ['status', 'requested_at']
    search_fields = ['user__email', 'group__group_name']
    readonly_fields = ['user', 'group', 'requested_at', 'handled_by', 'handled_at']


@admin.register(GroupMembership)
class GroupMembershipAdmin(admin.ModelAdmin):
    list_display = ['user', 'group', 'joined_at']
    list_filter = ['joined_at']
    search_fields = ['user__email', 'group__group_name']
    readonly_fields = ['user', 'group', 'joined_at']

@admin.register(SavingsGoal)
class SavingsGoalAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'target_amount', 'frequency', 'target_date', 'created_at']
    list_filter = ['frequency', 'created_at']
    search_fields = ['name', 'user__email']

@admin.register(GoalContribution)
class GoalContributionAdmin(admin.ModelAdmin):
    list_display = ['goal', 'amount', 'paid_at', 'is_verified']
    list_filter = ['paid_at', 'is_verified']
    search_fields = ['goal__name']
