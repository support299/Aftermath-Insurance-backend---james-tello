import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


class AppRole(models.TextChoices):
    ADMIN = "admin", "Admin"
    MANAGER = "manager", "Manager"
    AGENT = "agent", "Agent"


class User(AbstractUser):
    """Equivalent of Supabase auth.users — credentials only.

    All app-facing user data lives on Profile (the `profiles` table),
    exactly as in the original schema.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        db_table = "auth_users"
        indexes = [models.Index(fields=["email"])]

    def __str__(self) -> str:
        return self.email or self.username


class Profile(models.Model):
    user = models.OneToOneField(
        User,
        primary_key=True,
        on_delete=models.CASCADE,
        db_column="id",
        related_name="profile",
    )
    display_name = models.TextField()
    email = models.TextField(null=True, blank=True)
    phone = models.TextField(null=True, blank=True)
    team = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="team_id",
        related_name="profiles",
    )
    must_change_password = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "profiles"
        indexes = [models.Index(fields=["team"])]

    def __str__(self) -> str:
        return self.display_name


class UserRole(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, db_column="user_id", related_name="roles"
    )
    role = models.TextField(choices=AppRole.choices)

    class Meta:
        db_table = "user_roles"
        constraints = [
            models.UniqueConstraint(fields=["user", "role"], name="user_roles_user_id_role_key"),
        ]
        indexes = [models.Index(fields=["user"]), models.Index(fields=["role"])]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.role}"


class LoginToken(models.Model):
    """One-time login tokens used by the GHL auto-login (logid) flow."""

    token = models.TextField(primary_key=True)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, db_column="user_id", related_name="login_tokens"
    )
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "login_tokens"
        indexes = [models.Index(fields=["user"]), models.Index(fields=["expires_at"])]
