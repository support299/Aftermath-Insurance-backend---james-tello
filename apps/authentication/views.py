import secrets
from datetime import timedelta

from django.contrib.auth import authenticate
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.authentication.models import LoginToken, Profile, User, UserRole
from apps.ghl.models import GhlUser


def token_payload(user: User) -> dict:
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": {"id": str(user.id), "email": user.email},
    }


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or ""
        user = authenticate(request, username=email, password=password)
        if user is None:
            return Response(
                {"detail": "Invalid login credentials"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        return Response(token_payload(user))


class SignupView(APIView):
    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or ""
        metadata = request.data.get("data") or {}
        if not email or not password:
            return Response(
                {"detail": "Email and password are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if User.objects.filter(username=email).exists():
            return Response(
                {"detail": "User already registered"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = User.objects.create_user(username=email, email=email, password=password)
        # Mirrors the original handle_new_user trigger: profile + 'agent' role,
        # display_name falls back to the email local part.
        display_name = metadata.get("display_name") or email.split("@")[0]
        Profile.objects.create(user=user, display_name=display_name)
        UserRole.objects.create(user=user, role="agent")
        return Response(token_payload(user), status=status.HTTP_201_CREATED)


class LogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        refresh = request.data.get("refresh")
        if refresh:
            try:
                RefreshToken(refresh).blacklist()
            except TokenError:
                pass
        return Response({"detail": "ok"})


class UpdateUserView(APIView):
    """Equivalent of supabase.auth.updateUser: change own password/email."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        user: User = request.user
        password = request.data.get("password")
        email = request.data.get("email")
        if password:
            if len(password) < 6:
                return Response(
                    {"detail": "Password should be at least 6 characters."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            user.set_password(password)
        if email:
            user.email = email
            user.username = email
        user.save()
        return Response({"user": {"id": str(user.id), "email": user.email}})


class VerifyOtpView(APIView):
    """Exchanges a one-time login token (magiclink equivalent) for a session."""

    permission_classes = [AllowAny]

    def post(self, request):
        token_hash = request.data.get("token_hash") or ""
        row = (
            LoginToken.objects.select_related("user")
            .filter(token=token_hash, used_at__isnull=True, expires_at__gt=timezone.now())
            .first()
        )
        if row is None:
            return Response(
                {"detail": "Token has expired or is invalid"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        row.used_at = timezone.now()
        row.save(update_fields=["used_at"])
        return Response(token_payload(row.user))


class ExchangeLogidView(APIView):
    """Public endpoint used by the GHL auto-login flow (?logid=<ghl_users.id>)."""

    permission_classes = [AllowAny]

    def post(self, request):
        logid = (request.data.get("logid") or "").strip()
        if not logid:
            return Response({"error": "Missing logid"}, status=status.HTTP_400_BAD_REQUEST)

        ghl_user = GhlUser.objects.filter(id=logid).first()
        if not ghl_user or not ghl_user.app_user_id:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        user = User.objects.filter(id=ghl_user.app_user_id).first()
        if not user or not user.email:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        token = secrets.token_urlsafe(32)
        LoginToken.objects.create(
            token=token,
            user=user,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        return Response({"email": user.email, "token_hash": token})
