import uuid

from django.conf import settings
from django.db import models


class Target(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.TextField()  # "company" | "agent"
    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        db_column="agent_id",
        related_name="targets",
    )
    life_revenue_target = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    health_revenue_target = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    addon_revenue_target = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    life_attach_ratio_target = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    health_attach_ratio_target = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    addon_attach_ratio_target = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "targets"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(scope="company", agent__isnull=True)
                    | models.Q(scope="agent", agent__isnull=False)
                ),
                name="targets_scope_agent_chk",
            ),
            # Mirrors the original partial unique indexes
            models.UniqueConstraint(
                fields=["scope"],
                condition=models.Q(scope="company"),
                name="targets_company_unique",
            ),
            models.UniqueConstraint(
                fields=["agent"],
                condition=models.Q(scope="agent"),
                name="targets_agent_unique",
            ),
        ]
        indexes = [models.Index(fields=["scope"]), models.Index(fields=["agent"])]
