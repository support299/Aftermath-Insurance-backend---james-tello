"""Payout + 13-week tracker calculations."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from django.db.models import Min, Sum
from django.utils import timezone

from apps.catalog.models import AddOn, Product
from apps.company.models import CompanySettings
from apps.payouts.models import (
    AgentIncomeGoal,
    AgentMilestoneAward,
    CompLevel,
    OnboardingMilestone,
    ProductCommission,
    TrackerConfig,
)
from apps.sales.models import Sale


ZERO = Decimal("0")


def _reporting_tz() -> ZoneInfo:
    return ZoneInfo(CompanySettings.load().reporting_timezone)


def _d(val: Any) -> Decimal:
    if val is None or val == "":
        return ZERO
    try:
        return Decimal(str(val))
    except Exception:
        return ZERO


def monthly_from_line_item(item: dict) -> Decimal:
    """Prefer stored monthly_premium; else derive from annual amount."""
    if item.get("monthly_premium") not in (None, ""):
        base = _d(item["monthly_premium"])
        extra = _d(item.get("additional_price"))
        return base + extra
    annual = _d(item.get("amount"))
    if annual <= 0:
        return ZERO
    return (annual / Decimal("12")).quantize(Decimal("0.01"))


def calc_line_check(monthly: Decimal, advance_months: int, rate: Decimal) -> Decimal:
    if monthly <= 0 or advance_months <= 0 or rate <= 0:
        return ZERO
    return (monthly * Decimal(advance_months) * rate).quantize(Decimal("0.01"))


def _commission_lookup() -> tuple[dict[str, ProductCommission], dict[str, ProductCommission]]:
    """product_id → commission, product_name_lower → commission (first match)."""
    by_id: dict[str, ProductCommission] = {}
    by_name: dict[str, ProductCommission] = {}
    qs = ProductCommission.objects.filter(is_active=True).select_related("product", "add_on")
    for row in qs:
        if row.product_id:
            by_id[str(row.product_id)] = row
            name = (row.product.name if row.product else row.label or "").strip().lower()
            if name and name not in by_name:
                by_name[name] = row
        elif row.add_on_id:
            by_id[f"addon:{row.add_on_id}"] = row
            name = (row.add_on.name if row.add_on else row.label or "").strip().lower()
            if name and name not in by_name:
                by_name[name] = row
    return by_id, by_name


def resolve_commission(
    item: dict,
    *,
    products_by_name: dict[tuple[str, str], Product] | None = None,
    addons_by_name: dict[str, AddOn] | None = None,
    by_id: dict[str, ProductCommission] | None = None,
    by_name: dict[str, ProductCommission] | None = None,
) -> ProductCommission | None:
    if by_id is None or by_name is None:
        by_id, by_name = _commission_lookup()

    product_id = item.get("product_id") or item.get("catalog_product_id")
    if product_id and str(product_id) in by_id:
        return by_id[str(product_id)]

    kind = (item.get("kind") or "").lower()
    name = (item.get("product") or "").strip()
    carrier = (item.get("carrier") or "").strip()

    if kind == "addon":
        if addons_by_name and name.lower() in addons_by_name:
            aid = str(addons_by_name[name.lower()].id)
            if f"addon:{aid}" in by_id:
                return by_id[f"addon:{aid}"]
        return by_name.get(name.lower())

    if products_by_name:
        key = (carrier.lower(), name.lower())
        prod = products_by_name.get(key)
        if prod and str(prod.id) in by_id:
            return by_id[str(prod.id)]

    return by_name.get(name.lower())


def estimate_line_items_payout(
    line_items: list[dict],
    level_code: str | None = None,
    *,
    health_level_code: str | None = None,
    life_level_code: str | None = None,
) -> dict[str, Any]:
    """Return total + per-line breakdown for a set of sale line items.

    Health (+ add-on) lines use health_level_code; life lines use life_level_code.
    Legacy `level_code` fills both when track-specific codes are omitted.
    """
    health_code = health_level_code if health_level_code is not None else level_code
    life_code = life_level_code if life_level_code is not None else level_code

    by_id, by_name = _commission_lookup()
    products = {
        (p.carrier.name.lower() if p.carrier else "", p.name.lower()): p
        for p in Product.objects.select_related("carrier").filter(active=True)
    }
    addons = {a.name.lower(): a for a in AddOn.objects.filter(active=True)}

    lines: list[dict] = []
    total = ZERO
    for item in line_items or []:
        monthly = monthly_from_line_item(item)
        commission = resolve_commission(
            item,
            products_by_name=products,
            addons_by_name=addons,
            by_id=by_id,
            by_name=by_name,
        )
        kind = (item.get("kind") or "").lower()
        code = life_code if kind == "life" else health_code
        months = int(commission.advance_months) if commission else 0
        rate = commission.rate_for(code) if commission else ZERO
        check = calc_line_check(monthly, months, rate)
        total += check
        lines.append(
            {
                "product": item.get("product") or "",
                "kind": item.get("kind") or "",
                "monthly_premium": float(monthly),
                "advance_months": months,
                "rate": float(rate),
                "estimated_check": float(check),
                "matched": commission is not None,
                "commission_id": str(commission.id) if commission else None,
                "level_code": code,
                "level_track": "life" if kind == "life" else "health",
            }
        )

    return {
        "level_code": health_code,  # legacy
        "health_level_code": health_code,
        "life_level_code": life_code,
        "estimated_payout": float(total),
        "lines": lines,
    }


def agents_tracker_summaries(agent_ids: list) -> dict[str, dict[str, Any]]:
    """
    Lightweight per-agent tracker + payout summary for the Agents roster list.
    Batches sales queries instead of calling agent_tracker_payload N times.
    """
    if not agent_ids:
        return {}

    cfg = TrackerConfig.load()
    foundation = int(cfg.foundation_weeks)
    beacon = int(cfg.beacon_weeks)
    phase_goal = float(cfg.phase_goal)
    max_weeks = foundation + beacon
    tz = _reporting_tz()
    now = timezone.now()

    first_map = {
        str(r["agent_id"]): r["first"]
        for r in Sale.objects.filter(agent_id__in=agent_ids, reporting_only=False)
        .values("agent_id")
        .annotate(first=Min("sale_date"))
    }
    payout_map = {
        str(r["agent_id"]): float(_d(r["t"]))
        for r in Sale.objects.filter(agent_id__in=agent_ids, reporting_only=False)
        .values("agent_id")
        .annotate(t=Sum("estimated_payout"))
    }

    sales_by_agent: dict[str, list] = {str(aid): [] for aid in agent_ids}
    for s in Sale.objects.filter(agent_id__in=agent_ids, reporting_only=False).only(
        "agent_id", "sale_date", "deal_size"
    ):
        sales_by_agent[str(s.agent_id)].append(s)

    out: dict[str, dict[str, Any]] = {}
    for aid in agent_ids:
        key = str(aid)
        start = first_map.get(key)
        if not start:
            out[key] = {
                "first_sale_at": None,
                "current_week": 0,
                "phase": None,
                "phase_label": None,
                "phase_submitted": 0.0,
                "phase_pct": 0.0,
                "phase_goal": phase_goal,
                "tracker_active": False,
                "estimated_payout_ytd": payout_map.get(key, 0.0),
            }
            continue

        if timezone.is_naive(start):
            start = timezone.make_aware(start, tz)
        current_week = tracker_week_number(start, now)
        in_foundation = current_week <= foundation
        phase = 1 if in_foundation else 2
        phase_label = "Foundation" if in_foundation else "Beacon"
        week_lo = 1 if in_foundation else foundation + 1
        week_hi = foundation if in_foundation else foundation + beacon

        phase_submitted = ZERO
        for s in sales_by_agent.get(key, []):
            sd = s.sale_date
            if timezone.is_naive(sd):
                sd = timezone.make_aware(sd, tz)
            # Which week relative to start?
            start_day = start.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
            sale_day = sd.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
            w = ((sale_day.date() - start_day.date()).days // 7) + 1
            if week_lo <= w <= week_hi:
                phase_submitted += _d(s.deal_size)

        pct = min(100.0, float(phase_submitted) / phase_goal * 100) if phase_goal else 0.0
        out[key] = {
            "first_sale_at": start.isoformat(),
            "current_week": current_week,
            "phase": phase,
            "phase_label": phase_label,
            "phase_submitted": float(phase_submitted),
            "phase_pct": round(pct, 1),
            "phase_goal": phase_goal,
            "tracker_active": current_week <= max_weeks,
            "estimated_payout_ytd": payout_map.get(key, 0.0),
        }
    return out


def agent_comp_level_codes(agent_id) -> dict[str, str | None]:
    """Return {health, life} level codes for an agent."""
    from apps.authentication.models import Profile

    row = (
        Profile.objects.filter(pk=agent_id)
        .select_related("health_comp_level", "life_comp_level", "comp_level")
        .values(
            "health_comp_level__code",
            "life_comp_level__code",
            "comp_level__code",
        )
        .first()
    )
    if not row:
        return {"health": None, "life": None}
    health = row["health_comp_level__code"] or row["comp_level__code"]
    life = row["life_comp_level__code"]
    return {"health": health, "life": life}


def agent_comp_level_code(agent_id) -> str | None:
    """Legacy: health (or old single) level code."""
    return agent_comp_level_codes(agent_id).get("health")


def first_sale_at(agent_id) -> datetime | None:
    row = (
        Sale.objects.filter(agent_id=agent_id, reporting_only=False)
        .aggregate(first=Min("sale_date"))
    )
    return row["first"]


def tracker_week_number(start: datetime, as_of: datetime | None = None) -> int:
    """1-based week index from first sale; capped at foundation+beacon."""
    cfg = TrackerConfig.load()
    max_weeks = int(cfg.foundation_weeks) + int(cfg.beacon_weeks)
    if not start:
        return 0
    now = as_of or timezone.now()
    if timezone.is_naive(start):
        start = timezone.make_aware(start, _reporting_tz())
    if timezone.is_naive(now):
        now = timezone.make_aware(now, _reporting_tz())
    delta_days = (now.date() - start.date()).days
    week = (delta_days // 7) + 1
    return max(1, min(week, max_weeks))


def week_window(start: datetime, week_num: int) -> tuple[datetime, datetime]:
    """Inclusive start, exclusive end for week_num (1-based)."""
    tz = _reporting_tz()
    if timezone.is_naive(start):
        start = timezone.make_aware(start, tz)
    start_day = start.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    w_start = start_day + timedelta(days=7 * (week_num - 1))
    w_end = w_start + timedelta(days=7)
    return w_start, w_end


def agent_tracker_payload(agent_id) -> dict[str, Any]:
    cfg = TrackerConfig.load()
    foundation = int(cfg.foundation_weeks)
    beacon = int(cfg.beacon_weeks)
    phase_goal = float(cfg.phase_goal)

    start = first_sale_at(agent_id)
    if not start:
        return {
            "active": False,
            "reason": "no_sales",
            "first_sale_at": None,
            "current_week": 0,
            "phase": None,
            "phase_label": None,
            "phase_goal": phase_goal,
            "phase_submitted": 0,
            "phase_pct": 0,
            "projection": 0,
            "weeks": [],
            "foundation_weeks": foundation,
            "beacon_weeks": beacon,
        }

    current_week = tracker_week_number(start)
    in_foundation = current_week <= foundation
    phase = 1 if in_foundation else 2
    phase_label = "Foundation" if in_foundation else "Beacon"
    week_lo = 1 if in_foundation else foundation + 1
    week_hi = foundation if in_foundation else foundation + beacon

    sales = list(
        Sale.objects.filter(agent_id=agent_id, reporting_only=False)
        .only("sale_date", "deal_size")
        .order_by("sale_date")
    )

    weeks: list[dict] = []
    phase_submitted = ZERO
    for w in range(week_lo, week_hi + 1):
        w_start, w_end = week_window(start, w)
        total = ZERO
        for s in sales:
            sd = s.sale_date
            if timezone.is_naive(sd):
                sd = timezone.make_aware(sd, _reporting_tz())
            if w_start <= sd < w_end:
                total += _d(s.deal_size)
        phase_submitted += total
        weeks.append(
            {
                "week": w,
                "submitted": float(total),
                "is_current": w == current_week,
            }
        )

    weeks_elapsed = max(1, min(current_week, week_hi) - week_lo + 1)
    # Only count weeks from phase start through current (within phase)
    elapsed_in_phase = max(1, current_week - week_lo + 1) if current_week >= week_lo else 1
    projection = float(
        (phase_submitted / Decimal(elapsed_in_phase)) * Decimal(foundation)
        if phase_submitted > 0
        else ZERO
    )
    pct = min(100.0, float(phase_submitted) / phase_goal * 100) if phase_goal else 0.0

    active = current_week <= foundation + beacon

    return {
        "active": active,
        "reason": None if active else "past_window",
        "first_sale_at": start.isoformat(),
        "current_week": current_week,
        "phase": phase,
        "phase_label": phase_label,
        "phase_goal": phase_goal,
        "phase_submitted": float(phase_submitted),
        "phase_pct": round(pct, 1),
        "projection": round(projection, 2),
        "weeks": weeks,
        "foundation_weeks": foundation,
        "beacon_weeks": beacon,
        "weeks_elapsed_in_phase": weeks_elapsed,
    }


def agent_income_goal_payload(agent_id) -> dict[str, Any]:
    cfg = TrackerConfig.load()
    goal_row = AgentIncomeGoal.objects.filter(pk=agent_id).first()
    goal = float(goal_row.annual_income_goal) if goal_row else 0.0
    rate = float(cfg.blended_income_rate) or 0.18
    rate_d = Decimal(str(rate))
    business_needed = (goal / rate) if goal and rate else 0.0

    sales = Sale.objects.filter(agent_id=agent_id, reporting_only=False)
    submitted = sales.aggregate(t=Sum("deal_size"))["t"] or ZERO

    with_payout = sales.filter(estimated_payout__gt=0)
    payout_total = with_payout.aggregate(t=Sum("estimated_payout"))["t"] or ZERO
    ap_with_payout = with_payout.aggregate(t=Sum("deal_size"))["t"] or ZERO
    ap_fallback = _d(submitted) - _d(ap_with_payout)
    if ap_fallback < ZERO:
        ap_fallback = ZERO

    expected_from_commission = _d(payout_total)
    expected_from_fallback = (ap_fallback * rate_d).quantize(Decimal("0.01"))
    expected_income = expected_from_commission + expected_from_fallback
    pct = min(100.0, float(expected_income) / goal * 100) if goal else 0.0

    return {
        "annual_income_goal": goal,
        "blended_rate": rate,
        "business_needed": round(business_needed, 2),
        "submitted_ytd": float(_d(submitted)),
        "expected_income_blended": round(float(expected_income), 2),
        "expected_from_commission": round(float(expected_from_commission), 2),
        "expected_from_fallback": round(float(expected_from_fallback), 2),
        "estimated_payout_ytd": float(expected_from_commission),
        "progress_pct": round(pct, 1),
    }


def _product_match_count(sales: list[Sale], keyword: str) -> int:
    kw = (keyword or "").strip().lower()
    if not kw:
        return len(sales)
    n = 0
    for s in sales:
        parts = [s.product or "", s.carrier or ""]
        for item in s.line_items or []:
            parts.append(str(item.get("product") or ""))
            parts.append(str(item.get("carrier") or ""))
        if kw in " ".join(parts).lower():
            n += 1
    return n


def evaluate_milestones_for_agent(agent_id, *, triggering_sale: Sale | None = None) -> list[dict]:
    """Award any newly unlocked onboarding milestones. Returns newly awarded."""
    milestones = list(OnboardingMilestone.objects.filter(is_active=True).order_by("sort_order"))
    if not milestones:
        return []

    already = set(
        AgentMilestoneAward.objects.filter(agent_id=agent_id).values_list(
            "milestone_id", flat=True
        )
    )
    sales = list(
        Sale.objects.filter(agent_id=agent_id, reporting_only=False).order_by("sale_date", "created_at")
    )
    count = len(sales)
    submitted = sum((_d(s.deal_size) for s in sales), ZERO)
    newly: list[dict] = []

    for m in milestones:
        if m.id in already:
            continue
        unlocked = False
        if m.milestone_type == OnboardingMilestone.TYPE_FIRST_SALE and count >= 1:
            unlocked = True
        elif m.milestone_type == OnboardingMilestone.TYPE_SALE_COUNT and count >= int(m.threshold):
            unlocked = True
        elif m.milestone_type == OnboardingMilestone.TYPE_SUBMITTED_AP and submitted >= m.threshold:
            unlocked = True
        elif m.milestone_type == OnboardingMilestone.TYPE_PRODUCT_SALE:
            unlocked = _product_match_count(sales, m.match_value) >= int(m.threshold)

        if unlocked:
            award = AgentMilestoneAward.objects.create(
                agent_id=agent_id,
                milestone=m,
                sale=triggering_sale,
            )
            newly.append(
                {
                    "id": str(award.id),
                    "milestone_id": str(m.id),
                    "slug": m.slug,
                    "name": m.name,
                    "cash_reward": float(m.cash_reward),
                    "awarded_at": award.awarded_at.isoformat(),
                }
            )
    return newly


def agent_milestones_payload(agent_id) -> dict[str, Any]:
    defs = list(OnboardingMilestone.objects.filter(is_active=True).order_by("sort_order"))
    awards = {
        str(a.milestone_id): a
        for a in AgentMilestoneAward.objects.filter(agent_id=agent_id)
    }
    sales = list(
        Sale.objects.filter(agent_id=agent_id, reporting_only=False).order_by("sale_date")
    )
    count = len(sales)
    submitted = float(sum((_d(s.deal_size) for s in sales), ZERO))

    items = []
    for m in defs:
        award = awards.get(str(m.id))
        items.append(
            {
                "id": str(m.id),
                "slug": m.slug,
                "name": m.name,
                "description": m.description,
                "milestone_type": m.milestone_type,
                "threshold": float(m.threshold),
                "cash_reward": float(m.cash_reward),
                "earned": award is not None,
                "awarded_at": award.awarded_at.isoformat() if award else None,
            }
        )
    return {
        "sale_count": count,
        "submitted_ap": submitted,
        "milestones": items,
    }


def list_comp_levels_payload(track: str | None = None) -> list[dict]:
    qs = CompLevel.objects.order_by("track", "sort_order", "name")
    if track in ("health", "life"):
        qs = qs.filter(track=track)
    return [
        {
            "id": str(lv.id),
            "code": lv.code,
            "name": lv.name,
            "track": lv.track,
            "sort_order": lv.sort_order,
            "is_active": lv.is_active,
        }
        for lv in qs
    ]
