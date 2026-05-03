from .views import (
    ChangePasswordView, FullSignupView, SendOTPView, VerifyOTPView, CustomLoginView, ForgotPasswordView, ResetPasswordView, MeView
)
from rest_framework_simplejwt.views import TokenRefreshView
from django.urls import path

urlpatterns = [
    path('signup/', FullSignupView.as_view(), name='signup'),
    path('otp/send/', SendOTPView.as_view(), name='otp-send'),
    path('otp/verify/', VerifyOTPView.as_view(), name='otp-verify'),
    path('login/', CustomLoginView.as_view(), name='login'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('forgot-password/', ForgotPasswordView.as_view(), name='forgot_password'),
    path('reset-password/', ResetPasswordView.as_view(), name='reset_password'),
    path('change-password/', ChangePasswordView.as_view(), name='change-password'),
    path('me/', MeView.as_view(), name='me'),
]
