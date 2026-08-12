import uuid

from django.conf import settings
from django.db import models


class Expense(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        db_column="agent_id",
        related_name="expenses",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    start_date = models.DateField()
    end_date = models.DateField()
    notes = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "expenses"
        indexes = [
            models.Index(fields=["agent"], name="idx_expenses_agent_id"),
            models.Index(fields=["start_date", "end_date"], name="idx_expenses_dates"),
        ]


class CpaEntry(models.Model):
    """Weekly acquisition inputs used for CPA / ROI (not general expenses)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        db_column="agent_id",
        related_name="cpa_entries",
    )
    week_start = models.DateField(
        help_text="Monday of the week this entry covers (America/New_York week).",
    )
    leads_uploaded = models.PositiveIntegerField(default=0)
    lead_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Dollar amount spent texting these leads (entered by the agent).",
    )
    yes_count = models.PositiveIntegerField(default=0)
    quoted_count = models.PositiveIntegerField(default=0)
    sold_count = models.PositiveIntegerField(default=0)
    # check_amount = direct deposits (money made that week).
    check_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "cpa_entries"
        constraints = [
            models.UniqueConstraint(
                fields=["agent", "week_start"],
                name="cpa_entries_agent_week_start_key",
            ),
        ]
        indexes = [
            models.Index(fields=["agent"], name="idx_cpa_entries_agent_id"),
            models.Index(fields=["week_start"], name="idx_cpa_entries_week_start"),
        ]

    def __str__(self) -> str:
        return f"CpaEntry({self.agent_id} @ {self.week_start})"
