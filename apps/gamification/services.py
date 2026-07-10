"""Gamification engine: XP, levels, streaks, and activity events."""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.company.models import CompanySettings
from apps.gamification.models import ActivityEvent, AgentProgress, LevelDefinition
from apps.sales.models import Sale


def _reporting_tz() -> ZoneInfo:
    return ZoneInfo(CompanySettings.load().reporting_timezone)


def _ensure_datetime(dt) -> datetime.datetime:
    """Normalize DB values, API strings, or datetimes for timezone math."""
    if dt is None:
        raise ValueError("datetime value is required")
    if isinstance(dt, datetime.datetime):
        if timezone.is_naive(dt):
            return timezone.make_aware(dt, datetime.timezone.utc)
        return dt
    if isinstance(dt, str):
        from django.utils.dateparse import parse_datetime

        parsed = parse_datetime(dt.replace("Z", "+00:00")) or parse_datetime(dt)
        if parsed is None:
            raise ValueError(f"Invalid datetime: {dt}")
        if timezone.is_naive(parsed):
            return timezone.make_aware(parsed, datetime.timezone.utc)
        return parsed
    raise TypeError(f"Expected datetime or ISO string, got {type(dt).__name__}")


def _to_local_date(dt, tz: ZoneInfo) -> datetime.date:
    return _ensure_datetime(dt).astimezone(tz).date()


def premium_by_kind(line_items: list, kind: str) -> float:
    total = 0.0
    for item in line_items or []:
        if item.get("kind") == kind:
            total += float(item.get("amount") or 0)
    return total


def sale_xp(sale: Sale) -> int:
    """XP earned from a single sale."""
    from apps.gamification.config import get_earning_rules

    rules = get_earning_rules()
    line_items = sale.line_items or []
    total_premium = float(sale.deal_size or 0)
    life_premium = premium_by_kind(line_items, "life")
    health_premium = premium_by_kind(line_items, "health")
    addon_premium = premium_by_kind(line_items, "addon")

    xp = (
        total_premium * float(rules["xp_total_premium_mult"])
        + life_premium * float(rules["xp_life_bonus_mult"])
        + health_premium * float(rules["xp_health_bonus_mult"])
        + addon_premium * float(rules["xp_addon_bonus_mult"])
        + int(rules["xp_per_sale_base"])
    )
    return max(0, int(round(xp)))


def compute_streaks(sale_datetimes: list[datetime.datetime]) -> tuple[int, int]:
    """Return (current_streak, best_streak) from sale timestamps."""
    if not sale_datetimes:
        return 0, 0

    tz = _reporting_tz()
    unique_days = sorted({_to_local_date(dt, tz) for dt in sale_datetimes})

    best = 1
    run = 1
    for i in range(1, len(unique_days)):
        if (unique_days[i] - unique_days[i - 1]).days == 1:
            run += 1
            best = max(best, run)
        else:
            run = 1

    current = 1
    for i in range(len(unique_days) - 2, -1, -1):
        if (unique_days[i + 1] - unique_days[i]).days == 1:
            current += 1
        else:
            break

    return current, best


def level_for_xp(xp: int, levels: list[LevelDefinition] | None = None) -> LevelDefinition | None:
    if levels is None:
        levels = list(LevelDefinition.objects.order_by("rank"))
    if not levels:
        return None
    matched = levels[0]
    for level in levels:
        if xp >= level.xp_required:
            matched = level
        else:
            break
    return matched


def next_level(current: LevelDefinition | None, levels: list[LevelDefinition]) -> LevelDefinition | None:
    if not levels:
        return None
    if current is None:
        return levels[0]
    for level in levels:
        if level.rank > current.rank:
            return level
    return None


def progress_payload(progress: AgentProgress, levels: list[LevelDefinition] | None = None) -> dict:
    if levels is None:
        levels = list(LevelDefinition.objects.order_by("rank"))
    current = progress.level or level_for_xp(progress.total_xp, levels)
    nxt = next_level(current, levels)
    xp_to_next = max(0, nxt.xp_required - progress.total_xp) if nxt else 0
    xp_floor = current.xp_required if current else 0
    xp_ceiling = nxt.xp_required if nxt else progress.total_xp
    span = max(1, xp_ceiling - xp_floor)
    level_progress_pct = min(100.0, max(0.0, (progress.total_xp - xp_floor) / span * 100))

    return {
        "agent_id": str(progress.agent_id),
        "total_xp": progress.total_xp,
        "level_rank": current.rank if current else 0,
        "level_slug": current.slug if current else "unranked",
        "level_name": current.name if current else "Unranked",
        "level_tier": current.tier_type if current else "level",
        "xp_to_next": xp_to_next,
        "next_level_name": nxt.name if nxt else None,
        "level_progress_pct": round(level_progress_pct, 1),
        "current_streak": progress.current_streak,
        "best_streak": progress.best_streak,
        "points_balance": progress.points_balance,
        "bonus_xp": progress.bonus_xp,
    }


@transaction.atomic
def recalculate_agent(agent_id, *, emit_events: bool = False) -> AgentProgress:
    """Full recompute of an agent's XP, level, and streaks from all sales."""
    levels = list(LevelDefinition.objects.order_by("rank"))
    sales = Sale.objects.filter(agent_id=agent_id).order_by("sale_date")

    progress, _ = AgentProgress.objects.get_or_create(agent_id=agent_id)
    total_xp = sum(sale_xp(s) for s in sales) + int(progress.bonus_xp)
    sale_dates = [s.sale_date for s in sales if s.sale_date]
    current_streak, best_streak = compute_streaks(sale_dates)
    last_sale = sale_dates[-1] if sale_dates else None
    last_sale_date = _to_local_date(last_sale, _reporting_tz()) if last_sale else None
    new_level = level_for_xp(total_xp, levels)

    old_level = progress.level
    progress.total_xp = total_xp
    progress.level = new_level
    progress.current_streak = current_streak
    progress.best_streak = best_streak
    progress.last_sale_date = last_sale_date
    progress.save()

    if emit_events and new_level and old_level and old_level.pk != new_level.pk:
        _emit_level_up(progress, old_level, new_level)

    return progress


def _emit_level_up(progress: AgentProgress, old_level: LevelDefinition, new_level: LevelDefinition):
    agent_name = _agent_display_name(progress.agent_id)
    ActivityEvent.objects.create(
        event_type=ActivityEvent.EVENT_LEVEL_UP,
        agent_id=progress.agent_id,
        agent_name=agent_name,
        message=f"{agent_name} reached {new_level.name}!",
        metadata={
            "old_level": old_level.slug,
            "new_level": new_level.slug,
            "new_level_name": new_level.name,
            "total_xp": progress.total_xp,
        },
    )


def _agent_display_name(agent_id) -> str:
    from apps.authentication.models import Profile

    profile = Profile.objects.filter(user_id=agent_id).first()
    if profile and profile.display_name:
        return profile.display_name
    sale = Sale.objects.filter(agent_id=agent_id).order_by("-sale_date").first()
    if sale and sale.agent_name:
        return sale.agent_name
    return str(agent_id)


def _is_first_sale_of_day(sale: Sale) -> bool:
    tz = _reporting_tz()
    day = _to_local_date(sale.sale_date, tz)
    start = datetime.datetime.combine(day, datetime.time.min, tzinfo=tz)
    end = datetime.datetime.combine(day, datetime.time.max, tzinfo=tz)
    earlier = (
        Sale.objects.filter(sale_date__gte=start, sale_date__lte=end)
        .exclude(pk=sale.pk)
        .exists()
    )
    return not earlier


@transaction.atomic
def handle_sale_change(sale: Sale, *, is_delete: bool = False):
    """Recalculate agent progress and emit activity after a sale change."""
    from apps.gamification.points import earn_sale_points, reverse_sale_points

    if is_delete:
        reverse_sale_points(sale.id, sale.agent_id)
        recalculate_agent(sale.agent_id)
        return

    if sale.sale_date and not isinstance(sale.sale_date, datetime.datetime):
        sale.sale_date = _ensure_datetime(sale.sale_date)

    progress = recalculate_agent(sale.agent_id)
    earn_sale_points(sale)
    agent_name = sale.agent_name or _agent_display_name(sale.agent_id)

    ActivityEvent.objects.create(
        event_type=ActivityEvent.EVENT_SALE,
        agent_id=sale.agent_id,
        agent_name=agent_name,
        message=f"{agent_name} logged a sale",
        metadata={
            "sale_id": str(sale.id),
            "deal_size": float(sale.deal_size or 0),
            "product": sale.product,
        },
    )

    if _is_first_sale_of_day(sale):
        ActivityEvent.objects.create(
            event_type=ActivityEvent.EVENT_FIRST_BLOOD,
            agent_id=sale.agent_id,
            agent_name=agent_name,
            message=f"{agent_name} drew First Blood — first sale of the day!",
            metadata={"sale_id": str(sale.id)},
        )
        from apps.gamification.achievements import award_first_blood

        award_first_blood(sale.agent_id, sale.sale_date)

    from apps.gamification.incentives import sync_weekly_challenge_points

    sync_weekly_challenge_points(sale.agent_id)

    from apps.gamification.achievements import evaluate_all_time_achievements

    evaluate_all_time_achievements(sale.agent_id)

    if progress.current_streak in (5, 10, 15, 20, 30):
        ActivityEvent.objects.create(
            event_type=ActivityEvent.EVENT_STREAK,
            agent_id=sale.agent_id,
            agent_name=agent_name,
            message=f"{agent_name} is on a {progress.current_streak}-day streak!",
            metadata={"streak": progress.current_streak},
        )


DEFAULT_LEVELS = [
    (1, "rookie", "Rookie", 0, LevelDefinition.TIER_LEVEL, "Welcome to the board"),
    (2, "prospect", "Prospect", 2_500, LevelDefinition.TIER_LEVEL, "Building momentum"),
    (3, "contender", "Contender", 7_500, LevelDefinition.TIER_LEVEL, "In the hunt"),
    (4, "grinder", "Grinder", 15_000, LevelDefinition.TIER_LEVEL, "Putting in the work"),
    (5, "closer", "Closer", 30_000, LevelDefinition.TIER_LEVEL, "Deals are closing"),
    (6, "assassin", "Assassin", 50_000, LevelDefinition.TIER_LEVEL, "Precision selling"),
    (7, "veteran", "Veteran", 75_000, LevelDefinition.TIER_LEVEL, "Seasoned pro"),
    (8, "elite", "Elite", 120_000, LevelDefinition.TIER_LEVEL, "Top-tier performer"),
    (9, "legend", "Legend", 200_000, LevelDefinition.TIER_LEVEL, "Legendary status"),
    (10, "prestige-1", "Prestige I", 300_000, LevelDefinition.TIER_PRESTIGE, "Prestige unlocked"),
    (11, "prestige-2", "Prestige II", 450_000, LevelDefinition.TIER_PRESTIGE, "Prestige II"),
    (12, "prestige-3", "Prestige III", 650_000, LevelDefinition.TIER_PRESTIGE, "Prestige III"),
    (13, "prestige-4", "Prestige IV", 900_000, LevelDefinition.TIER_PRESTIGE, "Prestige IV"),
    (14, "prestige-5", "Prestige V", 1_200_000, LevelDefinition.TIER_PRESTIGE, "Prestige V"),
    (15, "hall-of-fame", "Hall of Fame", 2_000_000, LevelDefinition.TIER_HOF, "Immortalized"),
]


def seed_level_definitions() -> int:
    created = 0
    for rank, slug, name, xp, tier, desc in DEFAULT_LEVELS:
        _, was_created = LevelDefinition.objects.update_or_create(
            rank=rank,
            defaults={
                "slug": slug,
                "name": name,
                "xp_required": xp,
                "tier_type": tier,
                "description": desc,
            },
        )
        if was_created:
            created += 1
    return created
