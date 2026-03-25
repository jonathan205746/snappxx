# accounts/urls.py

from .views import(
    AnalyticsView, ContributeView, CreateSavingsGoalView, CreateSavingsGroupView, DashboardView, GroupDetailView,
    AllGroupsListView, GroupJoinRequestView, GroupRequestsListView, GroupRequestActionView,
    GoalsDashboardView, GoalDetailView,ContributeToGoalView, GroupsStatsView, JoinRequestsStatsView,
    MyJoinedGroupsListView, ProfileUpdateView, run_daily_payouts, run_reconciliation, run_clear_idempotency,
    run_goal_reminders,
)

from django_qstash.views import QStashWebhookView

from django.urls import path

urlpatterns = [
    path('profile/', ProfileUpdateView.as_view(), name='profile-update'),
    path('analytics/', AnalyticsView.as_view(), name='analytics'),

    path('groups/create/', CreateSavingsGroupView.as_view(), name='create-savings-group'),
    path('groups/my-joined/', MyJoinedGroupsListView.as_view(), name='my-joined-groups'),
    path('groups/all/', AllGroupsListView.as_view(), name='all-groups'),
    path('groups/<str:public_id>/', GroupDetailView.as_view(), name='group-detail'),

    # Join group request endpoints
    path('groups/<int:group_id>/request_join/', GroupJoinRequestView.as_view(), name='group-request-join'),
    path('groups/<int:group_id>/requests/', GroupRequestsListView.as_view(), name='group-requests-list'),
    path('groups/requests/<int:pk>/action/', GroupRequestActionView.as_view(), name='group-request-action'),
    path('groups/<int:group_id>/contribute/', ContributeView.as_view(), name='group-contribute'),
    path('groups/stats/', GroupsStatsView.as_view(), name='groups-stats'),
    path('groups/join-requests/stats/', JoinRequestsStatsView.as_view(), name='join-requests-stats'),

    # Dashboard endpoint
    path('dashboard/', DashboardView.as_view(), name='dashboard'),

    # Goals endpoint
    path('goals/create/', CreateSavingsGoalView.as_view(), name='create-savings-goal'),
    path('goals/dashboard/', GoalsDashboardView.as_view(), name='goals-dashboard'),
    path('goals/<int:goal_id>/contribute/', ContributeToGoalView.as_view(), name='goal-contribute'),
    path('goals/<int:id>/', GoalDetailView.as_view(), name='goal-detail'),

    path('qstash/webhook/', QStashWebhookView.as_view(), name='qstash-webhook'),
    path('tasks/run-daily-payouts/', run_daily_payouts, name='run-daily-payouts'),
    path('tasks/run-reconciliation/', run_reconciliation, name='run-reconciliation'),
    path('tasks/run-clear-idempotency/', run_clear_idempotency, name='run-clear-idempotency'),
    path('tasks/run-goal-reminders/', run_goal_reminders, name='run-goal-reminders'),
]
