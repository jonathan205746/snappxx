# accounts/urls.py

from .views import (
    AnalyticsView, AIChatView, ContributeView, CreateSavingsGoalView,
    CreateSavingsGroupView, DashboardView, GroupDetailView,
    AllGroupsListView, GroupJoinRequestView, GroupRequestsListView,
    GroupRequestActionView, GoalsDashboardView, GoalDetailView,
    ContributeToGoalView, GroupsStatsView, JoinRequestsStatsView,
    MyJoinedGroupsListView, ProfileUpdateView, RecentActivityView,
    ScheduledContributionsView, run_daily_payouts,
    run_reconciliation, run_clear_idempotency, run_goal_reminders,
    NotificationListView, NotificationMarkReadView,
    NotificationMarkAllReadView, NotificationUnreadCountView,
    NotificationDeleteView, NotificationClearAllView,
    GroupInviteLinkView, InvitePreviewView, InviteAcceptView,
)

from django.urls import path
from .payment_urls import urlpatterns as payment_urlpatterns
from .views import run_retry_failed_payouts, run_sync_pending_transfers

urlpatterns = [
    # Profile & Analytics
    path('profile/', ProfileUpdateView.as_view(), name='profile-update'),
    path('analytics/', AnalyticsView.as_view(), name='analytics'),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),

    # Groups — list / create
    path('groups/create/', CreateSavingsGroupView.as_view(), name='create-savings-group'),
    path('groups/my-joined/', MyJoinedGroupsListView.as_view(), name='my-joined-groups'),
    path('groups/all/', AllGroupsListView.as_view(), name='all-groups'),

    # Stats endpoints
    path('groups/stats/', GroupsStatsView.as_view(), name='groups-stats'),
    path('groups/join-requests/stats/', JoinRequestsStatsView.as_view(), name='join-requests-stats'),

    # Join request actions (numeric PK on the request row, not the group)
    path('groups/requests/<int:pk>/action/', GroupRequestActionView.as_view(), name='group-request-action'),

    # Invite link management — must be before the generic <str:public_id> catch-all
    path('groups/<str:group_id>/invite/', GroupInviteLinkView.as_view(), name='group-invite-link'),

    # ── Invite public endpoints ──────────────────────────────────────────────
    path('invite/<str:token>/', InvitePreviewView.as_view(), name='invite-preview'),
    path('invite/<str:token>/accept/', InviteAcceptView.as_view(), name='invite-accept'),
    # ────────────────────────────────────────────────────────────────────────

    path('groups/<str:group_id>/request_join/', GroupJoinRequestView.as_view(), name='group-request-join'),
    path('groups/<int:group_id>/requests/', GroupRequestsListView.as_view(), name='group-requests-list'),

    # KEY FIX: was <int:group_id>; now <str:group_id> so public_id strings are accepted.
    path('groups/<str:group_id>/contribute/', ContributeView.as_view(), name='group-contribute'),

    # Dynamic group detail (public_id or legacy numeric pk) — generic catch-all last
    path('groups/<str:public_id>/', GroupDetailView.as_view(), name='group-detail'),

    # Goals
    path('goals/create/', CreateSavingsGoalView.as_view(), name='create-savings-goal'),
    path('goals/dashboard/', GoalsDashboardView.as_view(), name='goals-dashboard'),
    path('goals/<int:goal_id>/contribute/', ContributeToGoalView.as_view(), name='goal-contribute'),
    path('goals/<int:id>/', GoalDetailView.as_view(), name='goal-detail'),

    path('activity/', RecentActivityView.as_view(), name='recent-activity'),
    path('scheduled-contributions/', ScheduledContributionsView.as_view(), name='scheduled-contributions'),

    # Background task triggers (cron)
    path('tasks/run-daily-payouts/', run_daily_payouts, name='run-daily-payouts'),
    path('tasks/run-reconciliation/', run_reconciliation, name='run-reconciliation'),
    path('tasks/run-clear-idempotency/', run_clear_idempotency, name='run-clear-idempotency'),
    path('tasks/run-goal-reminders/', run_goal_reminders, name='run-goal-reminders'),

    # AI Chat
    path('ai/chat/', AIChatView.as_view(), name='ai-chat'),

    # Notifications
    path('notifications/', NotificationListView.as_view(), name='notification-list'),
    path('notifications/clear/', NotificationClearAllView.as_view(), name='notification-clear-all'),
    path('notifications/unread-count/', NotificationUnreadCountView.as_view(), name='notification-unread-count'),
    path('notifications/mark-all-read/', NotificationMarkAllReadView.as_view(), name='notification-mark-all-read'),
    path('notifications/<int:pk>/read/', NotificationMarkReadView.as_view(), name='notification-mark-read'),
    path('notifications/<int:pk>/delete/', NotificationDeleteView.as_view(), name='notification-delete'),

    path('tasks/run-retry-failed-payouts/', run_retry_failed_payouts, name='run-retry-failed-payouts'),
    path('tasks/run-sync-pending-transfers/', run_sync_pending_transfers, name='run-sync-pending-transfers'),

    # Payments (Paystack integration)
    *payment_urlpatterns,
]
