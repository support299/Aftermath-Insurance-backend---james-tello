"""Server-side leaderboard aggregation — avoids shipping raw sales to the client."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from apps.authentication.models import Profile
from apps.expenses.models import Expense
from apps.gamification.models import ActivityEvent, AgentProgress, LevelDefinition
from apps.gamification.services import progress_payload
from apps.sales.models import Sale
from apps.teams.models import Team


def _num(value) -> float | None:
    return float(value) if value is not None else None


def _premium_by_kind(line_items: list, kind: str) -> float:
    total = 0.0
    for item in line_items or []:
        if item.get("kind") == kind:
            total += float(item.get("amount") or 0)
    return total


def _count_by_kind(line_items: list, kind: str) -> int:
    return sum(1 for item in (line_items or []) if item.get("kind") == kind)


def _sale_has_addon(sale, addon_name: str | None = None) -> bool:
    line_items = sale.line_items or []
    for item in line_items:
        if item.get("kind") == "addon":
            if not addon_name or item.get("product") == addon_name:
                return True
    add_ons = sale.add_ons or []
    if not addon_name:
        return len(add_ons) > 0
    return addon_name in add_ons


@dataclass
class AgentAccumulator:
    agent_id: str
    agent_name: str
    team_id: str | None
    team_name: str
    revenue: float = 0.0
    count: int = 0
    life_count: int = 0
    health_count: int = 0
    addon_count: int = 0
    life_revenue: float = 0.0
    health_revenue: float = 0.0
    addon_revenue: float = 0.0
    cpa_lead_cost: float = 0.0


@dataclass
class TeamAccumulator:
    team_id: str | None
    team_name: str
    revenue: float = 0.0
    count: int = 0
    cpa_lead_cost: float = 0.0


def _passes_filters(sale, filters: dict[str, str]) -> bool:
    carrier = filters.get("carrier", "all")
    product = filters.get("product", "all")
    lead_source = filters.get("lead_source", "all")
    addon = filters.get("addon", "all")
    team = filters.get("team", "all")

    if carrier != "all" and sale.carrier != carrier:
        return False
    if product != "all" and sale.product != product:
        return False
    if lead_source != "all" and (sale.lead_source or "") != lead_source:
        return False
    if addon != "all":
        if addon == "__none":
            if _sale_has_addon(sale):
                return False
        elif not _sale_has_addon(sale, addon):
            return False
    if team != "all":
        sale_team = str(sale.team_id) if sale.team_id else "none"
        if sale_team != team:
            return False
    return True


def build_leaderboard_payload(
    date_from=None,
    date_to=None,
    *,
    filters: dict[str, str] | None = None,
    include_sales: bool = False,
    activity_limit: int = 30,
) -> dict[str, Any]:
    filters = filters or {}

    sales_qs = Sale.objects.all()
    if date_from:
        sales_qs = sales_qs.filter(sale_date__gte=date_from)
    if date_to:
        sales_qs = sales_qs.filter(sale_date__lte=date_to)

    sales_qs = sales_qs.only(
        "id",
        "agent_id",
        "agent_name",
        "team_id",
        "team_name",
        "sale_date",
        "deal_size",
        "carrier",
        "product",
        "add_ons",
        "line_items",
        "lead_source",
        "cost_per_lead",
    )

    agents: dict[str, AgentAccumulator] = {}
    teams: dict[str, TeamAccumulator] = {}
    carriers: set[str] = set()
    products: set[str] = set()
    lead_sources: set[str] = set()
    addons: set[str] = set()
    raw_sales: list[dict] = []
    sale_count = 0

    for sale in sales_qs.iterator(chunk_size=1000):
        sale_count += 1
        carriers.add(sale.carrier)
        products.add(sale.product)
        if sale.lead_source:
            lead_sources.add(sale.lead_source)
        for a in sale.add_ons or []:
            addons.add(a)
        for item in sale.line_items or []:
            if item.get("kind") == "addon" and item.get("product"):
                addons.add(item["product"])

        if not _passes_filters(sale, filters):
            continue

        agent_id = str(sale.agent_id)
        team_id = str(sale.team_id) if sale.team_id else None
        team_key = team_id or "none"
        line_items = sale.line_items or []
        deal = float(sale.deal_size or 0)
        lead_cost = float(sale.cost_per_lead or 0)

        if agent_id not in agents:
            agents[agent_id] = AgentAccumulator(
                agent_id=agent_id,
                agent_name=sale.agent_name,
                team_id=team_id,
                team_name=sale.team_name or "Unassigned",
            )
        ag = agents[agent_id]
        ag.revenue += deal
        ag.count += 1
        ag.life_count += _count_by_kind(line_items, "life")
        ag.health_count += _count_by_kind(line_items, "health")
        ag.addon_count += _count_by_kind(line_items, "addon")
        ag.life_revenue += _premium_by_kind(line_items, "life")
        ag.health_revenue += _premium_by_kind(line_items, "health")
        ag.addon_revenue += _premium_by_kind(line_items, "addon")
        ag.cpa_lead_cost += lead_cost

        if team_key not in teams:
            teams[team_key] = TeamAccumulator(
                team_id=team_id,
                team_name=sale.team_name or "Unassigned",
            )
        tm = teams[team_key]
        tm.revenue += deal
        tm.count += 1
        tm.cpa_lead_cost += lead_cost

        if include_sales:
            raw_sales.append(
                {
                    "id": str(sale.id),
                    "agent_id": agent_id,
                    "agent_name": sale.agent_name,
                    "team_id": team_id,
                    "team_name": sale.team_name,
                    "sale_date": sale.sale_date.isoformat() if sale.sale_date else None,
                    "deal_size": deal,
                    "carrier": sale.carrier,
                    "product": sale.product,
                    "add_ons": sale.add_ons or [],
                    "line_items": line_items,
                    "lead_source": sale.lead_source,
                    "cost_per_lead": _num(sale.cost_per_lead),
                }
            )

    # Merge in agents with zero sales in range
    profile_rows = list(Profile.objects.select_related("team").order_by("display_name"))
    team_name_by_id = {str(t.id): t.name for t in Team.objects.all()}
    for p in profile_rows:
        aid = str(p.user_id)
        if aid not in agents:
            tid = str(p.team_id) if p.team_id else None
            agents[aid] = AgentAccumulator(
                agent_id=aid,
                agent_name=p.display_name,
                team_id=tid,
                team_name=team_name_by_id.get(tid, "Unassigned") if tid else "Unassigned",
            )

    for t in Team.objects.all():
        key = str(t.id)
        if key not in teams:
            teams[key] = TeamAccumulator(team_id=key, team_name=t.name)

    expense_by_agent: dict[str, float] = defaultdict(float)
    expenses_qs = Expense.objects.all()
    if date_to:
        expenses_qs = expenses_qs.filter(start_date__lte=date_to.date())
    if date_from:
        expenses_qs = expenses_qs.filter(end_date__gte=date_from.date())
    for e in expenses_qs.only("agent_id", "amount"):
        expense_by_agent[str(e.agent_id)] += float(e.amount)

    def agent_row(a: AgentAccumulator) -> dict:
        exp = expense_by_agent.get(a.agent_id, 0.0)
        return {
            "agent_id": a.agent_id,
            "agent_name": a.agent_name,
            "team_id": a.team_id,
            "team_name": a.team_name,
            "revenue": round(a.revenue, 2),
            "count": a.count,
            "avgDeal": round(a.revenue / a.count, 2) if a.count else 0,
            "lifeCount": a.life_count,
            "healthCount": a.health_count,
            "addonCount": a.addon_count,
            "lifeRevenue": round(a.life_revenue, 2),
            "healthRevenue": round(a.health_revenue, 2),
            "addonRevenue": round(a.addon_revenue, 2),
            "cpa": round(exp / a.count, 2) if a.count else 0,
        }

    agent_stats = sorted(
        [agent_row(a) for a in agents.values()],
        key=lambda x: (-x["revenue"], -x["count"], x["agent_name"]),
    )

    team_stats = sorted(
        [
            {
                "team_id": t.team_id,
                "team_name": t.team_name,
                "revenue": round(t.revenue, 2),
                "count": t.count,
                "avgDeal": round(t.revenue / t.count, 2) if t.count else 0,
                "cpa": round(t.cpa_lead_cost / t.count, 2) if t.count else 0,
            }
            for t in teams.values()
        ],
        key=lambda x: (-x["revenue"], -x["count"], x["team_name"]),
    )

    progress_rows = list(AgentProgress.objects.select_related("level"))
    levels = list(LevelDefinition.objects.order_by("rank"))
    progress = {str(p.agent_id): progress_payload(p, levels) for p in progress_rows}

    activity = [
        {
            "id": str(e.id),
            "event_type": e.event_type,
            "agent_id": str(e.agent_id) if e.agent_id else None,
            "agent_name": e.agent_name,
            "message": e.message,
            "metadata": e.metadata or {},
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in ActivityEvent.objects.all()[:activity_limit]
    ]

    payload: dict[str, Any] = {
        "agent_stats": agent_stats,
        "team_stats": team_stats,
        "teams": [{"id": str(t.id), "name": t.name} for t in Team.objects.all().order_by("name")],
        "profiles": [
            {
                "id": str(p.user_id),
                "display_name": p.display_name,
                "team_id": str(p.team_id) if p.team_id else None,
            }
            for p in profile_rows
        ],
        "filter_options": {
            "carriers": sorted(carriers),
            "products": sorted(products),
            "lead_sources": sorted(lead_sources),
            "addons": sorted(addons),
        },
        "progress": progress,
        "activity": activity,
        "meta": {"sale_count": sale_count, "filtered_sale_count": sum(a.count for a in agents.values())},
    }

    if include_sales:
        payload["sales"] = raw_sales

    return payload
