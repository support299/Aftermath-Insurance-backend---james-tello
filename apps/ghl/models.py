import uuid

from django.conf import settings
from django.db import models


class GhlToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    access_token = models.TextField()
    refresh_token = models.TextField()
    expires_at = models.DateTimeField()
    location_id = models.TextField(null=True, blank=True)
    company_id = models.TextField(null=True, blank=True)
    user_type = models.TextField(null=True, blank=True)
    scope = models.TextField(null=True, blank=True)
    raw = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ghl_tokens"
        indexes = [models.Index(fields=["location_id"]), models.Index(fields=["expires_at"])]


class GhlUser(models.Model):
    id = models.TextField(primary_key=True)
    app_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="app_user_id",
        related_name="ghl_users",
    )
    location_id = models.TextField(null=True, blank=True)
    name = models.TextField(null=True, blank=True)
    email = models.TextField(null=True, blank=True)
    phone = models.TextField(null=True, blank=True)
    type = models.TextField(null=True, blank=True)
    raw = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ghl_users"
        indexes = [models.Index(fields=["app_user"]), models.Index(fields=["location_id"])]


class GhlContact(models.Model):
    id = models.TextField(primary_key=True)
    # GHL user id of the assigned user (ghl_users.id), kept as plain text like the original
    user_id = models.TextField(null=True, blank=True)
    location_id = models.TextField(null=True, blank=True)
    name = models.TextField(null=True, blank=True)
    email = models.TextField(null=True, blank=True)
    phone = models.TextField(null=True, blank=True)
    type = models.TextField(null=True, blank=True)
    raw = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ghl_contacts"
        indexes = [models.Index(fields=["user_id"]), models.Index(fields=["location_id"])]


class GhlWebhookLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.TextField()  # "success" | "error" | "skipped"
    type = models.TextField(null=True, blank=True)
    entity_id = models.TextField(null=True, blank=True)
    entity_table = models.TextField(null=True, blank=True)
    action = models.TextField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    payload = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ghl_webhook_logs"
        indexes = [models.Index(fields=["status"]), models.Index(fields=["-created_at"])]
