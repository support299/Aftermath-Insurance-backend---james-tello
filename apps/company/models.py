from decimal import Decimal

from django.db import models


class CompanySettings(models.Model):
    """Singleton row (pk=1) holding company-wide configuration."""

    SINGLETON_PK = 1

    id = models.PositiveSmallIntegerField(primary_key=True, default=SINGLETON_PK, editable=False)
    reporting_timezone = models.CharField(max_length=64, default="America/New_York")
    sms_cost_per_lead = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        default=Decimal("0.0102"),
        help_text="Dollar cost per lead/text used for CPA spend (leads × rate).",
    )
    cpa_cost_per_sale_target = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("160.00"),
        help_text="Cost per sale is considered good when at or under this amount.",
    )
    cpa_roi_target_multiple = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("3.00"),
        help_text="ROI (deposits ÷ lead cost) is considered good at or above this multiple.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "company_settings"

    def __str__(self) -> str:
        return f"CompanySettings(tz={self.reporting_timezone})"

    @classmethod
    def load(cls) -> "CompanySettings":
        obj, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return obj
