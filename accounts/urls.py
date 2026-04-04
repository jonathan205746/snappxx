# accounts/urls.py

from .views import (
    AnalyticsView, AIChatView, ContributeView, CreateSavingsGoalView,
    CreateSavingsGroupView, DashboardView, GroupDetailView,
    AllGroupsListView, GroupJoinRequestView, GroupRequestsListView,
    GroupRequestActionView, GoalsDashboardView, GoalDetailView,
    ContributeToGoalView, GroupsStatsView, JoinRequestsStatsView,
    MyJoinedGroupsListView, ProfileUpdateView, RecentActivityView, ScheduledContributionsView, run_daily_payouts,
    run_reconciliation, run_clear_idempotency, run_goal_reminders,
    NotificationListView, NotificationMarkReadView,
    NotificationMarkAllReadView, NotificationUnreadCountView,
    NotificationDeleteView, NotificationClearAllView, GroupInviteLinkView, InvitePreviewView
)

from django.urls import path

urlpatterns = [
    # Profile & Analytics
    path('profile/', ProfileUpdateView.as_view(), name='profile-update'),
    path('analytics/', AnalyticsView.as_view(), name='analytics'),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),

    # Groups
    path('groups/create/', CreateSavingsGroupView.as_view(), name='create-savings-group'),
    path('groups/my-joined/', MyJoinedGroupsListView.as_view(), name='my-joined-groups'),
    path('groups/all/', AllGroupsListView.as_view(), name='all-groups'),

    # Stats endpoints
    path('groups/stats/', GroupsStatsView.as_view(), name='groups-stats'),
    path('groups/join-requests/stats/', JoinRequestsStatsView.as_view(), name='join-requests-stats'),

    # Join request
    path('groups/requests/<int:pk>/action/', GroupRequestActionView.as_view(), name='group-request-action'),

    path('groups/<str:group_id>/invite/', GroupInviteLinkView.as_view(), name='group-invite-link'),
    path('invite/<str:token>/', InvitePreviewView.as_view(), name='invite-preview'),

    # Dynamic group detail
    path('groups/<str:public_id>/', GroupDetailView.as_view(), name='group-detail'),

    # Group-scoped
    path('groups/<int:group_id>/request_join/', GroupJoinRequestView.as_view(), name='group-request-join'),
    path('groups/<int:group_id>/requests/', GroupRequestsListView.as_view(), name='group-requests-list'),
    path('groups/<int:group_id>/contribute/', ContributeView.as_view(), name='group-contribute'),

    # Goals
    path('goals/create/', CreateSavingsGoalView.as_view(), name='create-savings-goal'),
    path('goals/dashboard/', GoalsDashboardView.as_view(), name='goals-dashboard'),
    path('goals/<int:goal_id>/contribute/', ContributeToGoalView.as_view(), name='goal-contribute'),
    path('goals/<int:id>/', GoalDetailView.as_view(), name='goal-detail'),

    path('activity/', RecentActivityView.as_view(), name='recent-activity'),
    path('scheduled-contributions/', ScheduledContributionsView.as_view(), name='scheduled-contributions'),

    # Background task triggers
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
]
