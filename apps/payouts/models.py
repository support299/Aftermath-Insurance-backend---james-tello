import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models


class CompLevel(models.Model):
    """Agent compensation tier (e.g. Level 1–4, Leader). Rates key off `code`."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=32, unique=True)
    name = models.CharField(max_length=64)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "comp_levels"
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name


class ProductCommission(models.Model):
    """
    Advance months + commission % by CompLevel.code for a catalog product or add-on.

    Formula: monthly_premium × advance_months × rate(level) = estimated check.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.OneToOneField(
        "catalog.Product",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        db_column="product_id",
        related_name="commission",
    )
    add_on = models.OneToOneField(
        "catalog.AddOn",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        db_column="add_on_id",
        related_name="commission",
    )
    # Fallback label when product/add_on name changes, or for display
    label = models.CharField(max_length=128, blank=True, default="")
    advance_months = models.PositiveSmallIntegerField(default=6)
    # {"L1": 0.20, "L2": 0.22, "LEADER": 0.28}
    rates = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_commissions"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(product_id__isnull=False, add_on_id__isnull=True)
                    | models.Q(product_id__isnull=True, add_on_id__isnull=False)
                ),
                name="product_commissions_xor_product_addon",
            ),
        ]

    def __str__(self) -> str:
        if self.product_id:
            return f"Commission: {self.product}"
        if self.add_on_id:
            return f"Commission: {self.add_on}"
        return self.label or str(self.id)

    def rate_for(self, level_code: str | None) -> Decimal:
        if not level_code:
            return Decimal("0")
        raw = (self.rates or {}).get(level_code)
        if raw is None:
            return Decimal("0")
        return Decimal(str(raw))


class TrackerConfig(models.Model):
    """Singleton: 13-week / Beacon phase goals."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    foundation_weeks = models.PositiveSmallIntegerField(default=13)
    beacon_weeks = models.PositiveSmallIntegerField(default=13)
    phase_goal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("250000"))
    blended_income_rate = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        default=Decimal("0.15"),
        help_text="Used for income-goal → business-needed estimate.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tracker_config"

    @classmethod
    def load(cls) -> "TrackerConfig":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class AgentIncomeGoal(models.Model):
    agent = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        db_column="agent_id",
        related_name="income_goal",
        primary_key=True,
    )
    annual_income_goal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "agent_income_goals"


class OnboardingMilestone(models.Model):
    """Cash / badge milestones for an agent's first ~13 weeks / 3 months."""

    TYPE_FIRST_SALE = "first_sale"
    TYPE_SALE_COUNT = "sale_count"
    TYPE_SUBMITTED_AP = "submitted_ap"

    TYPE_CHOICES = [
        (TYPE_FIRST_SALE, "First sale"),
        (TYPE_SALE_COUNT, "Nth sale"),
        (TYPE_SUBMITTED_AP, "Submitted annual premium"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True, default="")
    milestone_type = models.CharField(max_length=32, choices=TYPE_CHOICES)
    threshold = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("1"),
        help_text="Sale count or AP dollars depending on type.",
    )
    cash_reward = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "onboarding_milestones"
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name


class AgentMilestoneAward(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        db_column="agent_id",
        related_name="milestone_awards",
    )
    milestone = models.ForeignKey(
        OnboardingMilestone,
        on_delete=models.CASCADE,
        db_column="milestone_id",
        related_name="awards",
    )
    awarded_at = models.DateTimeField(auto_now_add=True)
    sale = models.ForeignKey(
        "sales.Sale",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="sale_id",
        related_name="milestone_awards",
    )
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "agent_milestone_awards"
        constraints = [
            models.UniqueConstraint(
                fields=["agent", "milestone"],
                name="agent_milestone_awards_unique",
            ),
        ]
