from django.contrib import admin

from apps.gamification.models import (
    AchievementDefinition,
    ActivityEvent,
    AgentAchievement,
    AgentProgress,
    Contest,
    GamificationConfig,
    LevelDefinition,
    PointTransaction,
    Redemption,
    Reward,
)


@admin.register(LevelDefinition)
class LevelDefinitionAdmin(admin.ModelAdmin):
    list_display = ("rank", "name", "slug", "xp_required", "tier_type")
    ordering = ("rank",)


@admin.register(AgentProgress)
class AgentProgressAdmin(admin.ModelAdmin):
    list_display = ("agent", "level", "total_xp", "bonus_xp", "points_balance", "current_streak", "best_streak", "updated_at")
    list_filter = ("level",)
    search_fields = ("agent__email",)
    ordering = ("-total_xp",)


@admin.register(ActivityEvent)
class ActivityEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "agent_name", "message", "created_at")
    list_filter = ("event_type",)
    ordering = ("-created_at",)


@admin.register(AchievementDefinition)
class AchievementDefinitionAdmin(admin.ModelAdmin):
    list_display = ("sort_order", "name", "slug", "metric", "period", "threshold", "points_reward", "is_active")
    list_filter = ("period", "is_active")
    ordering = ("sort_order",)


@admin.register(AgentAchievement)
class AgentAchievementAdmin(admin.ModelAdmin):
    list_display = ("agent", "achievement", "period_key", "earned_at")
    list_filter = ("achievement",)
    ordering = ("-earned_at",)


@admin.register(Contest)
class ContestAdmin(admin.ModelAdmin):
    list_display = ("title", "team", "metric", "start_date", "end_date", "is_active")
    list_filter = ("is_active", "metric")
    ordering = ("-start_date",)


@admin.register(Reward)
class RewardAdmin(admin.ModelAdmin):
    list_display = ("sort_order", "name", "slug", "points_cost", "is_active")
    list_filter = ("is_active",)
    ordering = ("sort_order", "points_cost")


@admin.register(PointTransaction)
class PointTransactionAdmin(admin.ModelAdmin):
    list_display = ("agent", "amount", "reason", "description", "created_at")
    list_filter = ("reason",)
    search_fields = ("agent__email", "description")
    ordering = ("-created_at",)


@admin.register(Redemption)
class RedemptionAdmin(admin.ModelAdmin):
    list_display = ("agent", "reward", "points_cost", "status", "created_at", "reviewed_at")
    list_filter = ("status",)
    search_fields = ("agent__email", "reward__name")
    ordering = ("-created_at",)


@admin.register(GamificationConfig)
class GamificationConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "updated_at")
