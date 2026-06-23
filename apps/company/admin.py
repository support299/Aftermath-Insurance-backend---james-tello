from django.contrib import admin

from apps.company.models import CompanySettings


@admin.register(CompanySettings)
class CompanySettingsAdmin(admin.ModelAdmin):
    list_display = ("id", "reporting_timezone", "updated_at")
