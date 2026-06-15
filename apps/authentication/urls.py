from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from apps.authentication.views import (
    LoginView,
    LogoutView,
    SignupView,
    UpdateUserView,
    VerifyOtpView,
)

urlpatterns = [
    path("login/", LoginView.as_view(), name="auth-login"),
    path("signup/", SignupView.as_view(), name="auth-signup"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),
    path("token/refresh/", TokenRefreshView.as_view(), name="auth-token-refresh"),
    path("update-user/", UpdateUserView.as_view(), name="auth-update-user"),
    path("verify-otp/", VerifyOtpView.as_view(), name="auth-verify-otp"),
]
