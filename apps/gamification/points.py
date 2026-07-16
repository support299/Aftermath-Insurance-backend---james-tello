"""Points ledger: earn, spend, and balance tracking."""

from __future__ import annotations

from django.db import transaction

from apps.gamification.models import (
    ActivityEvent,
    AgentProgress,
    PointTransaction,
    Redemption,
    Reward,
)
from apps.gamification.services import _agent_display_name
from apps.sales.models import Sale


class InsufficientPointsError(Exception):
    pass


def sale_points(sale: Sale) -> int:
    """Points earned from logging a sale."""
    from apps.gamification.config import get_earning_rules

    rules = get_earning_rules()
    premium = float(sale.deal_size or 0)
    base = int(rules["points_per_sale_base"])
    per100 = int(rules["points_per_100_premium"])
    minimum = int(rules["points_per_sale_min"])
    return max(minimum, base + int(premium // 100) * per100)


def points_for_achievement_slug(slug: str) -> int:
    from apps.gamification.config import FALLBACK_BADGE_POINTS, get_earning_rules
    from apps.gamification.models import AchievementDefinition

    ach = AchievementDefinition.objects.filter(slug=slug).only("points_reward").first()
    if ach and ach.points_reward is not None:
        return int(ach.points_reward)
    rules = get_earning_rules()
    return int(FALLBACK_BADGE_POINTS.get(slug, rules["default_badge_points"]))


@transaction.atomic
def _adjust_balance(agent_id, delta: int) -> AgentProgress:
    progress, _ = AgentProgress.objects.get_or_create(agent_id=agent_id)
    new_balance = int(progress.points_balance) + delta
    if new_balance < 0:
        raise InsufficientPointsError("Not enough points")
    progress.points_balance = new_balance
    progress.save(update_fields=["points_balance", "updated_at"])
    return progress


@transaction.atomic
def record_points(
    agent_id,
    amount: int,
    reason: str,
    *,
    description: str = "",
    reference_id=None,
    metadata: dict | None = None,
    emit_event: bool = False,
) -> PointTransaction | None:
    if amount == 0:
        return None

    tx = PointTransaction.objects.create(
        agent_id=agent_id,
        amount=amount,
        reason=reason,
        description=description,
        reference_id=reference_id,
        metadata=metadata or {},
    )
    _adjust_balance(agent_id, amount)

    if emit_event and amount > 0:
        name = _agent_display_name(agent_id)
        ActivityEvent.objects.create(
            event_type=ActivityEvent.EVENT_ACHIEVEMENT,
            agent_id=agent_id,
            agent_name=name,
            message=f"{name} earned {amount} points",
            metadata={"points": amount, "reason": reason},
        )
    return tx


def points_for_achievement(slug: str) -> int:
    return points_for_achievement_slug(slug)


@transaction.atomic
def earn_sale_points(sale: Sale) -> PointTransaction | None:
    exists = PointTransaction.objects.filter(
        agent_id=sale.agent_id,
        reason=PointTransaction.REASON_SALE,
        reference_id=sale.id,
    ).exists()
    if exists:
        return None
    pts = sale_points(sale)
    return record_points(
        sale.agent_id,
        pts,
        PointTransaction.REASON_SALE,
        description=f"Sale logged (+{pts} pts)",
        reference_id=sale.id,
        metadata={"sale_id": str(sale.id), "deal_size": float(sale.deal_size or 0)},
    )


@transaction.atomic
def reverse_sale_points(sale_id, agent_id) -> None:
    tx = PointTransaction.objects.filter(
        agent_id=agent_id,
        reason=PointTransaction.REASON_SALE,
        reference_id=sale_id,
    ).first()
    if not tx:
        return
    record_points(
        agent_id,
        -tx.amount,
        PointTransaction.REASON_ADJUSTMENT,
        description="Sale removed — points reversed",
        reference_id=sale_id,
        metadata={"sale_id": str(sale_id)},
    )
    tx.delete()


@transaction.atomic
def earn_achievement_points(
    agent_id,
    achievement_slug: str,
    reference_id=None,
    *,
    emit_event: bool = True,
    points_amount: int | None = None,
) -> PointTransaction | None:
    pts = points_amount if points_amount is not None else points_for_achievement(achievement_slug)
    return record_points(
        agent_id,
        pts,
        PointTransaction.REASON_ACHIEVEMENT,
        description=f"Badge earned: {achievement_slug}",
        reference_id=reference_id,
        metadata={"achievement_slug": achievement_slug},
        emit_event=emit_event,
    )


@transaction.atomic
def earn_challenge_points(agent_id, challenge_id: str, points: int) -> PointTransaction | None:
    dedupe_key = f"challenge:{challenge_id}"
    exists = PointTransaction.objects.filter(
        agent_id=agent_id,
        reason=PointTransaction.REASON_CHALLENGE,
        metadata__challenge_id=challenge_id,
    ).exists()
    if exists:
        return None
    return record_points(
        agent_id,
        points,
        PointTransaction.REASON_CHALLENGE,
        description=f"Weekly challenge complete: {challenge_id}",
        metadata={"challenge_id": challenge_id, "dedupe_key": dedupe_key},
        emit_event=True,
    )


@transaction.atomic
def request_redemption(agent_id, reward_id, agent_note: str = "") -> Redemption:
    reward = Reward.objects.select_related("team").get(pk=reward_id, is_active=True)
    if reward.team_id:
        from apps.authentication.models import Profile

        agent_team_id = Profile.objects.filter(pk=agent_id).values_list("team_id", flat=True).first()
        if str(agent_team_id or "") != str(reward.team_id):
            raise ValueError("This reward is only available to agents on that team")

    progress = AgentProgress.objects.filter(agent_id=agent_id).first()
    balance = progress.points_balance if progress else 0
    if balance < reward.points_cost:
        raise InsufficientPointsError("Not enough points for this reward")

    redemption = Redemption.objects.create(
        agent_id=agent_id,
        reward=reward,
        points_cost=reward.points_cost,
        status=Redemption.STATUS_PENDING,
        agent_note=agent_note.strip(),
    )
    record_points(
        agent_id,
        -reward.points_cost,
        PointTransaction.REASON_REDEMPTION,
        description=f"Redeemed: {reward.name}",
        reference_id=redemption.id,
        metadata={"reward_slug": reward.slug, "redemption_id": str(redemption.id)},
    )
    return redemption


@transaction.atomic
def review_redemption(redemption_id, reviewer_id, status: str, admin_note: str = "") -> Redemption:
    redemption = Redemption.objects.select_related("reward", "agent").get(pk=redemption_id)
    from django.utils import timezone

    if status == Redemption.STATUS_FULFILLED:
        if redemption.status != Redemption.STATUS_APPROVED:
            raise ValueError("Only approved redemptions can be marked fulfilled")
        redemption.status = status
        if admin_note.strip():
            redemption.admin_note = admin_note.strip()
        redemption.save()
        return redemption

    if redemption.status != Redemption.STATUS_PENDING:
        raise ValueError("Redemption already reviewed")

    redemption.status = status
    redemption.reviewed_by_id = reviewer_id
    redemption.reviewed_at = timezone.now()
    redemption.admin_note = admin_note.strip()
    redemption.save()

    if status == Redemption.STATUS_REJECTED:
        record_points(
            redemption.agent_id,
            redemption.points_cost,
            PointTransaction.REASON_REFUND,
            description=f"Refund: {redemption.reward.name}",
            reference_id=redemption.id,
            metadata={"redemption_id": str(redemption.id)},
        )
    return redemption


@transaction.atomic
def admin_adjust_points(agent_id, amount: int, description: str = "") -> PointTransaction | None:
    return record_points(
        agent_id,
        amount,
        PointTransaction.REASON_ADJUSTMENT,
        description=description or f"Admin adjustment ({amount:+d} pts)",
        emit_event=amount > 0,
    )


@transaction.atomic
def admin_adjust_bonus_xp(agent_id, delta: int, description: str = "") -> AgentProgress:
    progress, _ = AgentProgress.objects.get_or_create(agent_id=agent_id)
    progress.bonus_xp = int(progress.bonus_xp) + delta
    progress.save(update_fields=["bonus_xp", "updated_at"])
    from apps.gamification.services import recalculate_agent

    return recalculate_agent(agent_id)


DEFAULT_REWARDS = [
    ("coffee-gift-card", "Coffee gift card", "$10 card, any coffee shop", "☕", 300, 1),
    ("reserved-parking", "Reserved parking", "Best spot in the lot, 1 week", "🅿️", 600, 2),
    ("half-day-off", "Half-day off", "Leave at noon, any Friday", "🏖️", 1200, 3),
    ("weekend-getaway", "Weekend getaway", "Two nights, partner destination", "✈️", 5000, 4),
]


def seed_rewards() -> int:
    created = 0
    for slug, name, desc, icon, cost, order in DEFAULT_REWARDS:
        _, was_created = Reward.objects.update_or_create(
            slug=slug,
            defaults={
                "name": name,
                "description": desc,
                "icon": icon,
                "points_cost": cost,
                "sort_order": order,
                "is_active": True,
            },
        )
        if was_created:
            created += 1
    return created
