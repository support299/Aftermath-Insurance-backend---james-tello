import uuid

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone


class Sale(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sale_id = models.TextField(unique=True)
    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        db_column="agent_id",
        related_name="sales",
    )
    agent_name = models.TextField()
    team = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="team_id",
        related_name="sales",
    )
    team_name = models.TextField(null=True, blank=True)
    sale_date = models.DateTimeField(default=timezone.now)
    customer_name = models.TextField(null=True, blank=True)
    ghl_contact_id = models.TextField(null=True, blank=True)
    deal_size = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    carrier = models.TextField()
    product = models.TextField()
    add_ons = ArrayField(models.TextField(), default=list)
    add_on_amounts = models.JSONField(default=dict)
    line_items = models.JSONField(default=list)
    lead_source = models.TextField(null=True, blank=True)
    cost_per_lead = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    reporting_only = models.BooleanField(
        default=False,
        help_text="When true, sale is recorded for reporting only — no GHL contact sync.",
    )
    import_batch_id = models.UUIDField(
        null=True,
        blank=True,
        help_text="Set on bulk-imported sales so the batch can be rolled back.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sales"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(deal_size__gte=0) | models.Q(deal_size__isnull=True),
                name="sales_deal_size_check",
            ),
        ]
        indexes = [
            models.Index(fields=["agent"], name="idx_sales_agent"),
            models.Index(fields=["team"], name="idx_sales_team"),
            models.Index(fields=["-sale_date"], name="idx_sales_date"),
            models.Index(fields=["import_batch_id"], name="idx_sales_import_batch"),
        ]

    def __str__(self) -> str:
        return self.sale_id
