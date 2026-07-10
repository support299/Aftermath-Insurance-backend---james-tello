"""Configurable gamification earning rules and weekly challenges."""

from __future__ import annotations

from apps.gamification.models import GamificationConfig
from apps.sales.models import Sale

DEFAULT_EARNING_RULES = {
    "xp_total_premium_mult": 1.0,
    "xp_life_bonus_mult": 1.5,
    "xp_health_bonus_mult": 2.0,
    "xp_addon_bonus_mult": 2.0,
    "xp_per_sale_base": 100,
    "points_per_sale_base": 10,
    "points_per_100_premium": 1,
    "points_per_sale_min": 5,
    "default_badge_points": 50,
}

DEFAULT_WEEKLY_CHALLENGES = [
    {
        "id": "health-3",
        "title": "Close 3 Health policies",
        "description": "Health add-on push this week",
        "metric": "health_policies",
        "target": 3,
        "points": 75,
        "is_active": True,
        "sort_order": 1,
    },
    {
        "id": "same-day-5",
        "title": "Log sales 5 days this week",
        "description": "Consistency challenge",
        "metric": "sale_days",
        "target": 5,
        "points": 40,
        "is_active": True,
        "sort_order": 2,
    },
    {
        "id": "addon-2",
        "title": "Add an add-on to 2 deals",
        "description": "Cross-sell challenge",
        "metric": "addon_deals",
        "target": 2,
        "points": 30,
        "is_active": True,
        "sort_order": 3,
    },
]

FALLBACK_BADGE_POINTS = {
    "top-closer": 150,
    "money-maker": 200,
    "kill-streak": 100,
    "first-blood": 75,
    "comeback-kid": 125,
    "fastest-level-up": 100,
    "on-fire": 150,
    "sharp-shooter": 250,
    "rising-star": 175,
    "ghost": 100,
}


def _merged_rules(stored: dict | None) -> dict:
    rules = dict(DEFAULT_EARNING_RULES)
    if stored:
        rules.update({k: v for k, v in stored.items() if k in DEFAULT_EARNING_RULES})
    return rules


def get_earning_rules() -> dict:
    cfg = GamificationConfig.load()
    return _merged_rules(cfg.earning_rules)


def get_weekly_challenge_defs() -> list[dict]:
    cfg = GamificationConfig.load()
    stored = cfg.weekly_challenges or []
    if not stored:
        return [dict(c) for c in DEFAULT_WEEKLY_CHALLENGES]
    return [dict(c) for c in stored if c.get("is_active", True)]


def earning_rules_payload() -> dict:
    return get_earning_rules()


def weekly_challenges_payload() -> list[dict]:
    cfg = GamificationConfig.load()
    stored = cfg.weekly_challenges or []
    if not stored:
        return [dict(c) for c in DEFAULT_WEEKLY_CHALLENGES]
    return [dict(c) for c in sorted(stored, key=lambda c: (c.get("sort_order", 0), c.get("id", "")))]


def compute_challenge_metric(agent_id: str, metric: str, week_start, week_end) -> int:
    from apps.gamification.services import _ensure_datetime

    sales = list(
        Sale.objects.filter(
            agent_id=agent_id, sale_date__gte=week_start, sale_date__lte=week_end
        ).only("line_items", "add_ons", "sale_date")
    )
    if metric == "health_policies":
        return sum(
            1 for s in sales
            for li in (s.line_items or [])
            if li.get("kind") == "health"
        )
    if metric == "sale_days":
        return len({_ensure_datetime(s.sale_date).date() for s in sales if s.sale_date})
    if metric == "addon_deals":
        return sum(
            1 for s in sales
            if any(li.get("kind") == "addon" for li in (s.line_items or []))
            or (s.add_ons and len(s.add_ons) > 0)
        )
    return 0


def weekly_challenges_for_agent(agent_id: str, week_start, week_end) -> list[dict]:
    out = []
    for c in get_weekly_challenge_defs():
        if not c.get("is_active", True):
            continue
        current = compute_challenge_metric(agent_id, c.get("metric", ""), week_start, week_end)
        out.append(
            {
                "id": c["id"],
                "title": c.get("title", c["id"]),
                "description": c.get("description", ""),
                "current": current,
                "target": int(c.get("target") or 0),
                "points": int(c.get("points") or 0),
            }
        )
    return out
