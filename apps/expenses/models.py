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
