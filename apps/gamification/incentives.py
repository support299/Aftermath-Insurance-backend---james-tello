"""Build the /incentives page payload."""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from apps.authentication.models import Profile
from apps.gamification.achievements import _agent_stats_in_range, _week_bounds
from apps.gamification.models import (
    AchievementDefinition,
    AgentAchievement,
    AgentProgress,
    Contest,
    LevelDefinition,
    Redemption,
    Reward,
)
from apps.gamification.achievements import (
    _period_key,
    earned_status_label,
    period_display_label,
)
from apps.gamification.config import earning_rules_payload, weekly_challenges_for_agent
from apps.gamification.points import earn_challenge_points
from apps.gamification.services import progress_payload, premium_by_kind, _reporting_tz
from apps.sales.models import Sale


def _contest_progress(contest: Contest, agent_id: str) -> dict:
    start = datetime.datetime.combine(contest.start_date, datetime.time.min, tzinfo=datetime.timezone.utc)
    end = datetime.datetime.combine(contest.end_date, datetime.time.max, tzinfo=datetime.timezone.utc)
    sales = Sale.objects.filter(
        agent_id=agent_id, sale_date__gte=start, sale_date__lte=end
    ).only("deal_size", "line_items")

    current = Decimal("0")
    for s in sales.iterator():
        if contest.metric == Contest.METRIC_SALE_COUNT:
            current += 1
        elif contest.metric == Contest.METRIC_LIFE_REVENUE:
            current += Decimal(str(premium_by_kind(s.line_items or [], "life")))
        elif contest.metric == Contest.METRIC_ADDON_REVENUE:
            current += Decimal(str(premium_by_kind(s.line_items or [], "addon")))
        else:
            current += Decimal(str(s.deal_size or 0))

    target = Decimal(str(contest.target_value)) if contest.target_value else None
    pct = float(current / target * 100) if target and target > 0 else None
    return {
        "current": float(current),
        "target": float(target) if target else None,
        "progress_pct": min(100.0, pct) if pct is not None else None,
    }


def sync_weekly_challenge_points(agent_id: str) -> int:
    """Award challenge bonuses when thresholds are met (once per week)."""
    ref = timezone.now().astimezone(_reporting_tz()).date()
    week_start, week_end = _week_bounds(ref)
    week_key = f"{ref.isocalendar().year}-W{ref.isocalendar().week:02d}"
    awarded = 0
    for challenge in weekly_challenges_for_agent(agent_id, week_start, week_end):
        if challenge["current"] >= challenge["target"]:
            challenge_id = f"{week_key}:{challenge['id']}"
            if earn_challenge_points(agent_id, challenge_id, challenge["points"]):
                awarded += 1
    return awarded


def build_incentives_payload(agent_id: str) -> dict:
    sync_weekly_challenge_points(agent_id)

    levels = list(LevelDefinition.objects.order_by("rank"))
    progress = AgentProgress.objects.filter(agent_id=agent_id).select_related("level").first()
    progress_data = progress_payload(progress) if progress else progress_payload(
        AgentProgress(agent_id=agent_id, total_xp=0)
    )

    ref_date = timezone.now().astimezone(_reporting_tz()).date()
    earnings = AgentAchievement.objects.filter(agent_id=agent_id).values_list(
        "achievement_id", "achievement__slug", "period_key"
    )
    period_keys_by_slug: dict[str, set[str]] = {}
    for ach_id, slug, period_key in earnings:
        period_keys_by_slug.setdefault(slug, set()).add(period_key)

    badges = []
    for ach in AchievementDefinition.objects.filter(is_active=True):
        keys = period_keys_by_slug.get(ach.slug, set())
        current_key = _period_key(ach.period, ref_date)
        earned_ever = bool(keys)
        earned_current = current_key in keys
        badges.append(
            {
                "slug": ach.slug,
                "name": ach.name,
                "description": ach.description,
                "icon": ach.icon,
                "period": ach.period,
                "period_label": period_display_label(ach.period),
                "earned": earned_ever,
                "earned_ever": earned_ever,
                "earned_current_period": earned_current,
                "times_earned": len(keys),
                "status_label": earned_status_label(
                    period=ach.period,
                    earned_current=earned_current,
                    earned_ever=earned_ever,
                ),
            }
        )

    today = timezone.now().date()
    agent_team_id = (
        Profile.objects.filter(pk=agent_id).values_list("team_id", flat=True).first()
    )
    contest_qs = Contest.objects.filter(is_active=True, end_date__gte=today).select_related("team")
    if agent_team_id:
        contest_qs = contest_qs.filter(Q(team__isnull=True) | Q(team_id=agent_team_id))
    else:
        contest_qs = contest_qs.filter(team__isnull=True)

    contests = []
    for c in contest_qs.order_by("end_date"):
        prog = _contest_progress(c, agent_id)
        contests.append(
            {
                "id": str(c.id),
                "title": c.title,
                "description": c.description,
                "prize_description": c.prize_description,
                "metric": c.metric,
                "start_date": c.start_date.isoformat(),
                "end_date": c.end_date.isoformat(),
                "target_value": float(c.target_value) if c.target_value else None,
                "team_name": c.team.name if c.team_id else None,
                "scope": "team" if c.team_id else "global",
                **prog,
            }
        )

    ref = timezone.now().astimezone(_reporting_tz()).date()
    week_start, week_end = _week_bounds(ref)
    challenges = weekly_challenges_for_agent(agent_id, week_start, week_end)

    rewards = [
        {
            "id": str(r.id),
            "slug": r.slug,
            "name": r.name,
            "description": r.description,
            "icon": r.icon,
            "points_cost": r.points_cost,
            "can_afford": (progress.points_balance if progress else 0) >= r.points_cost,
        }
        for r in Reward.objects.filter(is_active=True).order_by("sort_order", "points_cost")
    ]

    redemptions = [
        {
            "id": str(rd.id),
            "reward_id": str(rd.reward_id),
            "reward_name": rd.reward.name,
            "reward_icon": rd.reward.icon,
            "points_cost": rd.points_cost,
            "status": rd.status,
            "agent_note": rd.agent_note,
            "admin_note": rd.admin_note,
            "created_at": rd.created_at.isoformat(),
            "reviewed_at": rd.reviewed_at.isoformat() if rd.reviewed_at else None,
        }
        for rd in Redemption.objects.filter(agent_id=agent_id)
        .select_related("reward")
        .order_by("-created_at")[:20]
    ]

    earned_ever_count = sum(1 for b in badges if b["earned_ever"])
    earned_current_count = sum(1 for b in badges if b["earned_current_period"])
    points_balance = progress.points_balance if progress else 0
    return {
        "progress": progress_data,
        "levels": [
            {
                "rank": lv.rank,
                "slug": lv.slug,
                "name": lv.name,
                "xp_required": lv.xp_required,
                "tier_type": lv.tier_type,
                "description": lv.description,
            }
            for lv in levels
        ],
        "badges": badges,
        "badges_summary": {
            "earned": earned_ever_count,
            "earned_ever": earned_ever_count,
            "earned_current_period": earned_current_count,
            "total": len(badges),
        },
        "contests": contests,
        "weekly_challenges": challenges,
        "points_balance": points_balance,
        "earning_rules": earning_rules_payload(),
        "rewards": rewards,
        "redemptions": redemptions,
    }
