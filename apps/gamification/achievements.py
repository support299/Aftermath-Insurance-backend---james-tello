"""Achievement definitions, evaluation, and awarding."""

from __future__ import annotations

import datetime
from collections import defaultdict

from django.db.models import Count, Q, Sum
from django.db import transaction
from django.utils import timezone

from apps.authentication.models import Profile
from apps.company.models import CompanySettings
from apps.gamification.models import (
    AchievementDefinition,
    ActivityEvent,
    AgentAchievement,
    AgentProgress,
)
from apps.gamification.services import (
    _agent_display_name,
    _reporting_tz,
    _to_local_date,
    premium_by_kind,
)
from apps.sales.models import Sale


DEFAULT_ACHIEVEMENTS = [
    ("top-closer", "Top Closer", "Most apps in the period", "🏆", "sale_count", "weekly", None, 1),
    ("money-maker", "Money Maker", "Highest premium in the period", "💰", "revenue", "weekly", None, 2),
    ("kill-streak", "Kill Streak", "Longest active sales streak", "🔥", "streak", "weekly", None, 3),
    ("first-blood", "First Blood", "First sale of the day", "⚡", "first_blood", "daily", None, 4),
    ("comeback-kid", "Comeback Kid", "Biggest week-over-week improvement", "💣", "improvement", "weekly", None, 5),
    ("fastest-level-up", "Fastest Level Up", "Most XP gained this week", "🚀", "level_rank", "weekly", None, 6),
    ("on-fire", "On Fire", "10-day sales streak", "🔥", "streak", "all_time", 10, 7),
    ("sharp-shooter", "Sharp Shooter", "100 deals logged", "🎯", "sale_count", "all_time", 100, 8),
    ("rising-star", "Rising Star", "Most improved this month", "⭐", "improvement", "monthly", None, 9),
    ("dial-demon", "Dial Demon", "Most calls in a week", "📞", "sale_count", "weekly", None, 10),
    ("iron-man", "Iron Man", "Perfect attendance", "🛡️", "streak", "monthly", 20, 11),
    ("ghost", "Ghost", "Quietly crushing it", "👻", "revenue", "weekly", None, 12),
]

# Admin/agent copy — single source of truth for how each badge works.
BADGE_META: dict[str, dict] = {
    "top-closer": {
        "rule": "The agent with the most sales logged during the current week wins. One winner per week.",
        "trigger": "manual_weekly",
        "trigger_label": "Run weekly awards",
        "display_hint": "Agents see “Earned this week” when they win the current week.",
        "implemented": True,
    },
    "money-maker": {
        "rule": "The agent with the highest total premium (deal size) during the current week wins.",
        "trigger": "manual_weekly",
        "trigger_label": "Run weekly awards",
        "display_hint": "Resets each week — agents can win again next week.",
        "implemented": True,
    },
    "kill-streak": {
        "rule": "Among agents with sales this week, whoever has the longest active sales streak wins.",
        "trigger": "manual_weekly",
        "trigger_label": "Run weekly awards",
        "display_hint": "Based on current streak at award time, not historical best.",
        "implemented": True,
    },
    "first-blood": {
        "rule": "Awarded automatically to the agent who logs the first sale of the day (company reporting timezone).",
        "trigger": "automatic_sale",
        "trigger_label": "Automatic on sale",
        "display_hint": "Can be earned again each new day; collection shows “Earned today”.",
        "implemented": True,
    },
    "comeback-kid": {
        "rule": "Biggest week-over-week revenue improvement (this week vs last week). One winner per week.",
        "trigger": "manual_weekly",
        "trigger_label": "Run weekly awards",
        "display_hint": "Compares revenue delta between consecutive weeks.",
        "implemented": True,
    },
    "fastest-level-up": {
        "rule": "Planned: most XP gained during the current week. Not wired up yet.",
        "trigger": "manual_weekly",
        "trigger_label": "Run weekly awards (not implemented)",
        "display_hint": "Listed for future use — will not award until implemented.",
        "implemented": False,
    },
    "on-fire": {
        "rule": "Lifetime badge when an agent reaches a 10-day (or more) best sales streak.",
        "trigger": "automatic_sale",
        "trigger_label": "Automatic on sale + weekly run",
        "display_hint": "Permanent unlock once earned — shown as “Earned (lifetime)”.",
        "implemented": True,
    },
    "sharp-shooter": {
        "rule": "Lifetime badge when an agent logs 100 total deals.",
        "trigger": "automatic_sale",
        "trigger_label": "Automatic on sale + weekly run",
        "display_hint": "Permanent unlock — never resets.",
        "implemented": True,
    },
    "rising-star": {
        "rule": "Biggest month-over-month revenue improvement. One winner per month.",
        "trigger": "manual_monthly",
        "trigger_label": "Run weekly + monthly awards",
        "display_hint": "Agents see “Earned this month” when they win the current month.",
        "implemented": True,
    },
    "dial-demon": {
        "rule": "Same as Top Closer (most sales in the week). Off by default — enable if you want a second sales-count badge.",
        "trigger": "manual_weekly",
        "trigger_label": "Run weekly awards",
        "display_hint": "Inactive by default to avoid duplicating Top Closer.",
        "implemented": True,
    },
    "iron-man": {
        "rule": "Agents with a current sales streak of 20+ days during monthly evaluation.",
        "trigger": "manual_monthly",
        "trigger_label": "Run weekly + monthly awards",
        "display_hint": "Inactive by default. Monthly period.",
        "implemented": True,
    },
    "ghost": {
        "rule": "3rd-place agent by weekly revenue (needs 3+ active agents) — “quietly crushing it” without being #1.",
        "trigger": "manual_weekly",
        "trigger_label": "Run weekly awards",
        "display_hint": "Weekly winner; can be earned again next week.",
        "implemented": True,
    },
}


def badge_meta_for_slug(slug: str) -> dict:
    return BADGE_META.get(
        slug,
        {
            "rule": "Custom badge.",
            "trigger": "manual_weekly",
            "trigger_label": "Run weekly awards",
            "display_hint": "",
            "implemented": True,
        },
    )


def period_display_label(period: str) -> str:
    if period == AchievementDefinition.PERIOD_DAILY:
        return "today"
    if period == AchievementDefinition.PERIOD_WEEKLY:
        return "this week"
    if period == AchievementDefinition.PERIOD_MONTHLY:
        return "this month"
    return "lifetime"


def earned_status_label(*, period: str, earned_current: bool, earned_ever: bool) -> str | None:
    if earned_current:
        label = period_display_label(period)
        if period == AchievementDefinition.PERIOD_ALL_TIME:
            return "Earned (lifetime)"
        return f"Earned {label}"
    if earned_ever:
        label = period_display_label(period)
        if period == AchievementDefinition.PERIOD_ALL_TIME:
            return "Earned (lifetime)"
        return f"Earned before — win again {label}"
    return None


def seed_achievement_definitions() -> int:
    from apps.gamification.config import FALLBACK_BADGE_POINTS

    inactive = {"dial-demon", "iron-man"}
    created = 0
    for slug, name, desc, icon, metric, period, threshold, order in DEFAULT_ACHIEVEMENTS:
        _, was_created = AchievementDefinition.objects.update_or_create(
            slug=slug,
            defaults={
                "name": name,
                "description": desc,
                "icon": icon,
                "metric": metric,
                "period": period,
                "threshold": threshold,
                "sort_order": order,
                "is_active": slug not in inactive,
                "points_reward": FALLBACK_BADGE_POINTS.get(slug),
            },
        )
        if was_created:
            created += 1
    return created


def _period_key(period: str, dt: datetime.date | None = None) -> str:
    dt = dt or timezone.now().astimezone(_reporting_tz()).date()
    if period == AchievementDefinition.PERIOD_DAILY:
        return dt.isoformat()
    if period == AchievementDefinition.PERIOD_WEEKLY:
        return f"{dt.isocalendar().year}-W{dt.isocalendar().week:02d}"
    if period == AchievementDefinition.PERIOD_MONTHLY:
        return dt.strftime("%Y-%m")
    return "all_time"


def _week_bounds(dt: datetime.date) -> tuple[datetime.datetime, datetime.datetime]:
    tz = _reporting_tz()
    # Monday start
    start = dt - datetime.timedelta(days=dt.weekday())
    end = start + datetime.timedelta(days=6)
    start_dt = datetime.datetime.combine(start, datetime.time.min, tzinfo=tz)
    end_dt = datetime.datetime.combine(end, datetime.time.max, tzinfo=tz)
    return start_dt, end_dt


def _month_bounds(dt: datetime.date) -> tuple[datetime.datetime, datetime.datetime]:
    tz = _reporting_tz()
    start = dt.replace(day=1)
    if dt.month == 12:
        next_month = start.replace(year=dt.year + 1, month=1)
    else:
        next_month = start.replace(month=dt.month + 1)
    end = next_month - datetime.timedelta(days=1)
    start_dt = datetime.datetime.combine(start, datetime.time.min, tzinfo=tz)
    end_dt = datetime.datetime.combine(end, datetime.time.max, tzinfo=tz)
    return start_dt, end_dt


def _points_for_slugs(slugs: set[str]) -> dict[str, int]:
    from apps.gamification.config import FALLBACK_BADGE_POINTS, get_earning_rules

    rules = get_earning_rules()
    cache: dict[str, int] = {}
    for ach in AchievementDefinition.objects.filter(slug__in=slugs).only("slug", "points_reward"):
        if ach.points_reward is not None:
            cache[ach.slug] = int(ach.points_reward)
        else:
            cache[ach.slug] = int(FALLBACK_BADGE_POINTS.get(ach.slug, rules["default_badge_points"]))
    for slug in slugs:
        if slug not in cache:
            cache[slug] = int(FALLBACK_BADGE_POINTS.get(slug, rules["default_badge_points"]))
    return cache


def _empty_agent_stat() -> dict:
    return {"count": 0, "revenue": 0.0, "life_revenue": 0.0, "addon_revenue": 0.0, "name": ""}


def _aggregated_stats_two_ranges(
    cur_start: datetime.datetime,
    cur_end: datetime.datetime,
    prev_start: datetime.datetime,
    prev_end: datetime.datetime,
) -> tuple[dict[str, dict], dict[str, dict]]:
    """One GROUP BY query for current + previous period sale totals."""
    cur_stats: dict[str, dict] = {}
    prev_stats: dict[str, dict] = {}
    rows = (
        Sale.objects.filter(sale_date__gte=prev_start, sale_date__lte=cur_end)
        .values("agent_id")
        .annotate(
            cur_count=Count("id", filter=Q(sale_date__gte=cur_start, sale_date__lte=cur_end)),
            cur_revenue=Sum("deal_size", filter=Q(sale_date__gte=cur_start, sale_date__lte=cur_end)),
            prev_count=Count("id", filter=Q(sale_date__gte=prev_start, sale_date__lte=prev_end)),
            prev_revenue=Sum("deal_size", filter=Q(sale_date__gte=prev_start, sale_date__lte=prev_end)),
        )
    )
    for row in rows:
        aid = str(row["agent_id"])
        if row["cur_count"]:
            cur_stats[aid] = {
                **_empty_agent_stat(),
                "count": row["cur_count"],
                "revenue": float(row["cur_revenue"] or 0),
            }
        if row["prev_count"]:
            prev_stats[aid] = {
                **_empty_agent_stat(),
                "count": row["prev_count"],
                "revenue": float(row["prev_revenue"] or 0),
            }
    return cur_stats, prev_stats


def _already_awarded_slugs_for_period(period_key: str, period: str) -> set[str]:
    return set(
        AgentAchievement.objects.filter(
            period_key=period_key,
            achievement__period=period,
            achievement__is_active=True,
        ).values_list("achievement__slug", flat=True)
    )


def _already_awarded_lifetime_agents() -> dict[str, set]:
    rows = AgentAchievement.objects.filter(
        period_key="all_time",
        achievement__period=AchievementDefinition.PERIOD_ALL_TIME,
        achievement__is_active=True,
    ).values_list("achievement__slug", "agent_id")
    done: dict[str, set] = defaultdict(set)
    for slug, agent_id in rows:
        done[slug].add(agent_id)
    return done


def _agent_stats_in_range(
    date_from: datetime.datetime,
    date_to: datetime.datetime,
) -> dict[str, dict]:
    stats: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "revenue": 0.0, "life_revenue": 0.0, "addon_revenue": 0.0, "name": ""}
    )
    qs = Sale.objects.filter(sale_date__gte=date_from, sale_date__lte=date_to).only(
        "agent_id", "agent_name", "deal_size", "line_items"
    )
    for sale in qs.iterator(chunk_size=1000):
        aid = str(sale.agent_id)
        stats[aid]["count"] += 1
        stats[aid]["revenue"] += float(sale.deal_size or 0)
        stats[aid]["life_revenue"] += premium_by_kind(sale.line_items or [], "life")
        stats[aid]["addon_revenue"] += premium_by_kind(sale.line_items or [], "addon")
        stats[aid]["name"] = sale.agent_name
    return dict(stats)


def _winner_by_key(stats: dict[str, dict], key: str) -> str | None:
    if not stats:
        return None
    ranked = sorted(stats.items(), key=lambda x: (-x[1].get(key, 0), -x[1].get("count", 0)))
    if ranked[0][1].get(key, 0) <= 0:
        return None
    return ranked[0][0]


def _ghost_winner(stats: dict[str, dict], progress_map: dict[str, AgentProgress]) -> str | None:
    """High revenue but not #1 — 'quietly crushing it'."""
    if len(stats) < 3:
        return None
    ranked = sorted(stats.items(), key=lambda x: -x[1]["revenue"])
    if len(ranked) < 3:
        return None
    candidate_id, candidate = ranked[2]
    if candidate["revenue"] <= 0:
        return None
    top_id = ranked[0][0]
    if candidate_id == top_id:
        return ranked[1][0] if len(ranked) > 1 else None
    return candidate_id


def award_achievement(
    agent_id,
    achievement: AchievementDefinition,
    period_key: str,
    *,
    metadata: dict | None = None,
    emit_event: bool = True,
    points_amount: int | None = None,
) -> AgentAchievement | None:
    obj, created = AgentAchievement.objects.get_or_create(
        agent_id=agent_id,
        achievement=achievement,
        period_key=period_key,
        defaults={"metadata": metadata or {}},
    )
    if not created:
        return None

    from apps.gamification.points import earn_achievement_points

    earn_achievement_points(
        agent_id,
        achievement.slug,
        reference_id=obj.id,
        emit_event=emit_event,
        points_amount=points_amount,
    )

    if emit_event:
        name = _agent_display_name(agent_id)
        ActivityEvent.objects.create(
            event_type=ActivityEvent.EVENT_ACHIEVEMENT,
            agent_id=agent_id,
            agent_name=name,
            message=f"{name} earned the {achievement.name} badge!",
            metadata={
                "achievement_slug": achievement.slug,
                "achievement_name": achievement.name,
                "icon": achievement.icon,
                "period_key": period_key,
            },
        )
    return obj


def award_first_blood(agent_id, sale_date: datetime.datetime) -> None:
    ach = AchievementDefinition.objects.filter(
        slug="first-blood", is_active=True
    ).first()
    if not ach:
        return
    day = _to_local_date(sale_date, _reporting_tz())
    award_achievement(agent_id, ach, day.isoformat(), emit_event=True)


def evaluate_all_time_achievements(agent_id=None) -> int:
    """Check lifetime thresholds (100 deals, 10-day streak, etc.)."""
    achievements = {
        a.slug: a
        for a in AchievementDefinition.objects.filter(
            period=AchievementDefinition.PERIOD_ALL_TIME, is_active=True
        )
    }
    if not achievements:
        return 0

    already = _already_awarded_lifetime_agents()
    points_cache = _points_for_slugs(set(achievements.keys()))
    awarded = 0

    if agent_id is not None:
        sharp_ach = achievements.get("sharp-shooter")
        if sharp_ach and agent_id not in already.get("sharp-shooter", set()):
            sale_count = Sale.objects.filter(agent_id=agent_id).count()
            if sale_count >= 100 and award_achievement(
                agent_id,
                sharp_ach,
                "all_time",
                metadata={"sale_count": sale_count},
                emit_event=False,
                points_amount=points_cache.get("sharp-shooter"),
            ):
                awarded += 1

        on_fire_ach = achievements.get("on-fire")
        if on_fire_ach and agent_id not in already.get("on-fire", set()):
            progress = AgentProgress.objects.filter(agent_id=agent_id).only("best_streak").first()
            if progress and progress.best_streak >= 10 and award_achievement(
                agent_id,
                on_fire_ach,
                "all_time",
                metadata={"best_streak": progress.best_streak},
                emit_event=False,
                points_amount=points_cache.get("on-fire"),
            ):
                awarded += 1
        return awarded

    sharp_ach = achievements.get("sharp-shooter")
    if sharp_ach:
        sharp_done = already.get("sharp-shooter", set())
        candidates = (
            Sale.objects.values("agent_id")
            .annotate(sale_count=Count("id"))
            .filter(sale_count__gte=100)
            .exclude(agent_id__in=sharp_done)
        )
        for row in candidates:
            if award_achievement(
                row["agent_id"],
                sharp_ach,
                "all_time",
                metadata={"sale_count": row["sale_count"]},
                emit_event=False,
                points_amount=points_cache.get("sharp-shooter"),
            ):
                awarded += 1

    on_fire_ach = achievements.get("on-fire")
    if on_fire_ach:
        on_fire_done = already.get("on-fire", set())
        progress_qs = AgentProgress.objects.filter(best_streak__gte=10).exclude(
            agent_id__in=on_fire_done
        )
        for progress in progress_qs.only("agent_id", "best_streak"):
            if award_achievement(
                progress.agent_id,
                on_fire_ach,
                "all_time",
                metadata={"best_streak": progress.best_streak},
                emit_event=False,
                points_amount=points_cache.get("on-fire"),
            ):
                awarded += 1

    return awarded


def evaluate_period_achievements(period: str = "weekly", reference: datetime.date | None = None) -> int:
    """Award competitive period badges to winners."""
    reference = reference or timezone.now().astimezone(_reporting_tz()).date()
    pkey = _period_key(period, reference)
    awarded = 0

    if period == AchievementDefinition.PERIOD_WEEKLY:
        start, end = _week_bounds(reference)
        prev_start = start - datetime.timedelta(days=7)
        prev_end = start - datetime.timedelta(seconds=1)
    elif period == AchievementDefinition.PERIOD_MONTHLY:
        start, end = _month_bounds(reference)
        prev_month_end = start - datetime.timedelta(seconds=1)
        prev_start = datetime.datetime.combine(
            prev_month_end.date().replace(day=1),
            datetime.time.min,
            tzinfo=prev_month_end.tzinfo,
        )
        prev_end = prev_month_end
    else:
        return 0

    slug_map = {
        a.slug: a
        for a in AchievementDefinition.objects.filter(period=period, is_active=True)
    }
    if not slug_map:
        return 0

    already_awarded = _already_awarded_slugs_for_period(pkey, period)
    pending_slugs = {
        slug
        for slug in (set(slug_map.keys()) - already_awarded)
        if badge_meta_for_slug(slug).get("implemented", True)
    }
    if not pending_slugs:
        return 0

    needs_sale_stats = pending_slugs & {
        "top-closer",
        "money-maker",
        "dial-demon",
        "comeback-kid",
        "rising-star",
        "ghost",
    }
    if pending_slugs == {"ghost"}:
        active_agents = (
            Sale.objects.filter(sale_date__gte=start, sale_date__lte=end)
            .values("agent_id")
            .distinct()
            .count()
        )
        if active_agents < 3:
            return 0

    current_stats: dict[str, dict] = {}
    prev_stats: dict[str, dict] = {}
    if needs_sale_stats:
        current_stats, prev_stats = _aggregated_stats_two_ranges(start, end, prev_start, prev_end)
        if not current_stats:
            return 0
    elif pending_slugs & {"kill-streak", "iron-man"}:
        agent_ids = Sale.objects.filter(sale_date__gte=start, sale_date__lte=end).values_list(
            "agent_id", flat=True
        ).distinct()
        if not agent_ids:
            return 0
        current_stats = {str(aid): _empty_agent_stat() for aid in agent_ids}

    points_cache = _points_for_slugs(pending_slugs)
    progress_map = {}
    if pending_slugs & {"kill-streak", "ghost", "iron-man"}:
        progress_map = {
            str(p.agent_id): p
            for p in AgentProgress.objects.filter(
                agent_id__in=current_stats.keys()
            ).only("agent_id", "current_streak", "total_xp", "best_streak")
        }

    mappings = [
        ("top-closer", "count"),
        ("money-maker", "revenue"),
        ("dial-demon", "count"),
    ]
    for slug, key in mappings:
        if slug not in pending_slugs:
            continue
        ach = slug_map[slug]
        winner = _winner_by_key(current_stats, key)
        if winner and award_achievement(
            winner,
            ach,
            pkey,
            emit_event=False,
            points_amount=points_cache.get(slug),
        ):
            awarded += 1

    if "kill-streak" in pending_slugs and progress_map:
        streak_ach = slug_map["kill-streak"]
        streak_winner = max(
            progress_map.items(),
            key=lambda x: (x[1].current_streak, x[1].total_xp),
        )
        if streak_winner[1].current_streak > 0:
            if award_achievement(
                streak_winner[0],
                streak_ach,
                pkey,
                metadata={"streak": streak_winner[1].current_streak},
                emit_event=False,
                points_amount=points_cache.get("kill-streak"),
            ):
                awarded += 1

    comeback_slug = "comeback-kid" if period == AchievementDefinition.PERIOD_WEEKLY else "rising-star"
    if comeback_slug in pending_slugs and comeback_slug in slug_map:
        comeback_ach = slug_map[comeback_slug]
        best_delta = -1
        comeback_id = None
        for aid, cur in current_stats.items():
            prev_rev = prev_stats.get(aid, {}).get("revenue", 0)
            delta = cur["revenue"] - prev_rev
            if delta > best_delta:
                best_delta = delta
                comeback_id = aid
        if comeback_id and best_delta > 0:
            if award_achievement(
                comeback_id,
                comeback_ach,
                pkey,
                metadata={"improvement": best_delta},
                emit_event=False,
                points_amount=points_cache.get(comeback_slug),
            ):
                awarded += 1

    if "ghost" in pending_slugs:
        ghost_ach = slug_map["ghost"]
        ghost_id = _ghost_winner(current_stats, progress_map)
        if ghost_id and award_achievement(
            ghost_id,
            ghost_ach,
            pkey,
            emit_event=False,
            points_amount=points_cache.get("ghost"),
        ):
            awarded += 1

    if "iron-man" in pending_slugs and period == AchievementDefinition.PERIOD_MONTHLY:
        iron_ach = slug_map["iron-man"]
        threshold = iron_ach.threshold or 20
        iron_done = {
            str(aid)
            for aid in AgentAchievement.objects.filter(achievement=iron_ach, period_key=pkey).values_list(
                "agent_id", flat=True
            )
        }
        for aid, prog in progress_map.items():
            if aid in iron_done:
                continue
            if prog.current_streak >= threshold:
                if award_achievement(
                    aid,
                    iron_ach,
                    pkey,
                    metadata={"streak": prog.current_streak},
                    emit_event=False,
                    points_amount=points_cache.get("iron-man"),
                ):
                    awarded += 1

    return awarded


@transaction.atomic
def evaluate_all_achievements(*, include_monthly: bool = False, include_lifetime: bool = False) -> int:
    total = 0
    if include_lifetime:
        total += evaluate_all_time_achievements()
    total += evaluate_period_achievements("weekly")
    if include_monthly:
        total += evaluate_period_achievements("monthly")
    return total
