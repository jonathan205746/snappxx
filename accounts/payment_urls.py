from django.urls import path
from .payment_views import (
    CashOutView,
    PaymentStatusView,
    PaystackWebhookView,
    TopUpInitializeView,
)

urlpatterns = [
    # Money In
    # Frontend asks backend to initialize a Paystack transaction
    path(
        "payments/topup/initialize/",
        TopUpInitializeView.as_view(),
        name="topup-initialize",
    ),

    # Webhook
    # Paystack posts charge.success / transfer.*
    path(
        "payments/webhook/",
        PaystackWebhookView.as_view(),
        name="paystack-webhook",
    ),

    # Money Out (user-initiated)
    # User withdraws from their internal wallet to their MoMo account
    path(
        "payments/cashout/",
        CashOutView.as_view(),
        name="cashout",
    ),

    #Status polling (fallback)
    # Frontend polls this while waiting for webhook confirmation
    path(
        "payments/status/<str:reference>/",
        PaymentStatusView.as_view(),
        name="payment-status",
    ),
]
