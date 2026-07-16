import uuid

from django.conf import settings
from django.db import models


class LevelDefinition(models.Model):
    """Configurable level ladder (Rookie → Legend → Prestige → Hall of Fame)."""

    TIER_LEVEL = "level"
    TIER_PRESTIGE = "prestige"
    TIER_HOF = "hof"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rank = models.PositiveSmallIntegerField(unique=True)
    slug = models.SlugField(max_length=32, unique=True)
    name = models.CharField(max_length=64)
    xp_required = models.PositiveIntegerField(default=0)
    tier_type = models.CharField(
        max_length=16,
        choices=[
            (TIER_LEVEL, "Level"),
            (TIER_PRESTIGE, "Prestige"),
            (TIER_HOF, "Hall of Fame"),
        ],
        default=TIER_LEVEL,
    )
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "level_definitions"
        ordering = ["rank"]

    def __str__(self) -> str:
        return f"{self.rank}. {self.name} ({self.xp_required} XP)"


class AgentProgress(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        db_column="agent_id",
        related_name="gamification_progress",
    )
    total_xp = models.PositiveIntegerField(default=0)
    level = models.ForeignKey(
        LevelDefinition,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="agents_at_level",
    )
    current_streak = models.PositiveSmallIntegerField(default=0)
    best_streak = models.PositiveSmallIntegerField(default=0)
    last_sale_date = models.DateField(null=True, blank=True)
    points_balance = models.PositiveIntegerField(default=0)
    bonus_xp = models.IntegerField(default=0, help_text="Admin bonus XP added on top of sales-derived XP.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "agent_progress"
        indexes = [models.Index(fields=["-total_xp"])]

    def __str__(self) -> str:
        level_name = self.level.name if self.level else "Unranked"
        return f"{self.agent_id} — {level_name} ({self.total_xp} XP)"


class ActivityEvent(models.Model):
    EVENT_SALE = "sale_logged"
    EVENT_FIRST_BLOOD = "first_blood"
    EVENT_LEVEL_UP = "level_up"
    EVENT_STREAK = "streak_milestone"
    EVENT_ACHIEVEMENT = "achievement_unlocked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_type = models.CharField(max_length=32)
    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="agent_id",
        related_name="activity_events",
    )
    agent_name = models.TextField(blank=True, default="")
    message = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "activity_events"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["-created_at"])]

    def __str__(self) -> str:
        return f"{self.event_type}: {self.message[:60]}"


class AchievementDefinition(models.Model):
    PERIOD_DAILY = "daily"
    PERIOD_WEEKLY = "weekly"
    PERIOD_MONTHLY = "monthly"
    PERIOD_ALL_TIME = "all_time"

    METRIC_SALE_COUNT = "sale_count"
    METRIC_REVENUE = "revenue"
    METRIC_STREAK = "streak"
    METRIC_FIRST_BLOOD = "first_blood"
    METRIC_IMPROVEMENT = "improvement"
    METRIC_LEVEL_RANK = "level_rank"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=48, unique=True)
    name = models.CharField(max_length=64)
    description = models.TextField(blank=True, default="")
    icon = models.CharField(max_length=8, default="🏆")
    metric = models.CharField(max_length=32)
    period = models.CharField(max_length=16, default=PERIOD_WEEKLY)
    threshold = models.PositiveIntegerField(null=True, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    points_reward = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "achievement_definitions"
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name


class AgentAchievement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        db_column="agent_id",
        related_name="achievements",
    )
    achievement = models.ForeignKey(
        AchievementDefinition,
        on_delete=models.CASCADE,
        related_name="agent_earnings",
    )
    period_key = models.CharField(max_length=32, default="all_time")
    earned_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "agent_achievements"
        constraints = [
            models.UniqueConstraint(
                fields=["agent", "achievement", "period_key"],
                name="agent_achievements_unique_period",
            ),
        ]
        indexes = [
            models.Index(fields=["agent"]),
            models.Index(fields=["-earned_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.agent_id} — {self.achievement.slug} ({self.period_key})"


class Contest(models.Model):
    METRIC_REVENUE = "revenue"
    METRIC_SALE_COUNT = "sale_count"
    METRIC_LIFE_REVENUE = "life_revenue"
    METRIC_ADDON_REVENUE = "addon_revenue"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=128)
    description = models.TextField(blank=True, default="")
    prize_description = models.TextField(blank=True, default="")
    metric = models.CharField(max_length=32, default=METRIC_REVENUE)
    target_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)
    team = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        db_column="team_id",
        related_name="contests",
        help_text="Null = company-wide global contest visible to all agents.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "contests"
        ordering = ["-start_date"]
        indexes = [models.Index(fields=["team", "-start_date"])]

    def __str__(self) -> str:
        return self.title


class Reward(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=48, unique=True)
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True, default="")
    icon = models.CharField(max_length=8, default="🎁")
    points_cost = models.PositiveIntegerField()
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    team = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        db_column="team_id",
        related_name="rewards",
        help_text="Null = company-wide reward visible to all agents.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "rewards"
        ordering = ["sort_order", "points_cost"]
        indexes = [models.Index(fields=["team", "is_active"])]

    def __str__(self) -> str:
        return self.name


class PointTransaction(models.Model):
    REASON_SALE = "sale"
    REASON_ACHIEVEMENT = "achievement"
    REASON_CHALLENGE = "challenge"
    REASON_REDEMPTION = "redemption"
    REASON_REFUND = "refund"
    REASON_ADJUSTMENT = "adjustment"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        db_column="agent_id",
        related_name="point_transactions",
    )
    amount = models.IntegerField()
    reason = models.CharField(max_length=32)
    description = models.TextField(blank=True, default="")
    reference_id = models.UUIDField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "point_transactions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["agent", "-created_at"]),
            models.Index(fields=["reason"]),
        ]

    def __str__(self) -> str:
        sign = "+" if self.amount >= 0 else ""
        return f"{self.agent_id} {sign}{self.amount} ({self.reason})"


class Redemption(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_FULFILLED = "fulfilled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        db_column="agent_id",
        related_name="redemptions",
    )
    reward = models.ForeignKey(
        Reward,
        on_delete=models.PROTECT,
        related_name="redemptions",
    )
    points_cost = models.PositiveIntegerField()
    status = models.CharField(max_length=16, default=STATUS_PENDING)
    agent_note = models.TextField(blank=True, default="")
    admin_note = models.TextField(blank=True, default="")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_redemptions",
        db_column="reviewed_by_id",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "redemptions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["agent", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.agent_id} — {self.reward.name} ({self.status})"


class GamificationConfig(models.Model):
    """Singleton config for XP/points earning rules and weekly challenges."""

    SINGLETON_PK = 1

    id = models.PositiveSmallIntegerField(primary_key=True, default=SINGLETON_PK)
    earning_rules = models.JSONField(default=dict, blank=True)
    weekly_challenges = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "gamification_config"

    def __str__(self) -> str:
        return "Gamification config"

    @classmethod
    def load(cls) -> "GamificationConfig":
        from apps.gamification.config import DEFAULT_EARNING_RULES, DEFAULT_WEEKLY_CHALLENGES

        obj, created = cls.objects.get_or_create(
            pk=cls.SINGLETON_PK,
            defaults={
                "earning_rules": DEFAULT_EARNING_RULES,
                "weekly_challenges": DEFAULT_WEEKLY_CHALLENGES,
            },
        )
        if created:
            return obj
        changed = False
        if not obj.earning_rules:
            obj.earning_rules = DEFAULT_EARNING_RULES
            changed = True
        if not obj.weekly_challenges:
            obj.weekly_challenges = DEFAULT_WEEKLY_CHALLENGES
            changed = True
        if changed:
            obj.save()
        return obj
