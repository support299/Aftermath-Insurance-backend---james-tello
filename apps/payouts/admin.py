from django.contrib import admin

from apps.payouts.models import (
    AgentIncomeGoal,
    AgentMilestoneAward,
    CompLevel,
    OnboardingMilestone,
    ProductCommission,
    TrackerConfig,
)


@admin.register(CompLevel)
class CompLevelAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")


@admin.register(ProductCommission)
class ProductCommissionAdmin(admin.ModelAdmin):
    list_display = ("label", "product", "add_on", "advance_months", "is_active")
    list_filter = ("is_active", "advance_months")


@admin.register(TrackerConfig)
class TrackerConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "foundation_weeks", "beacon_weeks", "phase_goal")


@admin.register(AgentIncomeGoal)
class AgentIncomeGoalAdmin(admin.ModelAdmin):
    list_display = ("agent", "annual_income_goal", "updated_at")


@admin.register(OnboardingMilestone)
class OnboardingMilestoneAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "milestone_type", "threshold", "match_value", "cash_reward", "is_active")


@admin.register(AgentMilestoneAward)
class AgentMilestoneAwardAdmin(admin.ModelAdmin):
    list_display = ("agent", "milestone", "awarded_at")
    readonly_fields = ("awarded_at",)
