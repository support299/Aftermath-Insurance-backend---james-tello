from django.db import models


class CompanySettings(models.Model):
    """Singleton row (pk=1) holding company-wide configuration."""

    SINGLETON_PK = 1

    id = models.PositiveSmallIntegerField(primary_key=True, default=SINGLETON_PK, editable=False)
    reporting_timezone = models.CharField(max_length=64, default="America/New_York")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "company_settings"

    def __str__(self) -> str:
        return f"CompanySettings(tz={self.reporting_timezone})"

    @classmethod
    def load(cls) -> "CompanySettings":
        obj, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return obj
