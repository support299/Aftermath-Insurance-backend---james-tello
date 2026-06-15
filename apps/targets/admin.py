from django.contrib import admin

from apps.targets.models import Target


@admin.register(Target)
class TargetAdmin(admin.ModelAdmin):
    list_display = ("scope", "agent", "life_revenue_target", "health_revenue_target", "addon_revenue_target")
    list_filter = ("scope",)
