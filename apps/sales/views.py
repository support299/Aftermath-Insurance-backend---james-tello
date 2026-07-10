"""Leaderboard data endpoint.

Unlike the generic /api/db/ table API (which enforces per-row RLS so agents
only see their own sales), the leaderboard is intentionally company-wide: any
authenticated user sees every agent's and every team's numbers, exactly like an
admin. This is scoped to ONLY the data the Leaderboards page needs and does not
change visibility anywhere else (Sales page, Dashboard, etc. stay role-scoped).
"""

from django.core.cache import cache
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils.dateparse import parse_datetime
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authentication.models import Profile
from apps.dbapi.roles import is_admin, is_manager, managed_team_ids
from apps.sales.leaderboard_service import build_leaderboard_payload


class LeaderboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from_raw = request.query_params.get("from")
        to_raw = request.query_params.get("to")
        date_from = parse_datetime(from_raw) if from_raw else None
        date_to = parse_datetime(to_raw) if to_raw else None

        filters = {
            "carrier": request.query_params.get("carrier", "all"),
            "product": request.query_params.get("product", "all"),
            "lead_source": request.query_params.get("lead_source", "all"),
            "addon": request.query_params.get("addon", "all"),
            "team": request.query_params.get("team", "all"),
        }

        include_sales = request.query_params.get("include_sales") == "1"

        cache_key = "|".join(
            [
                "leaderboard",
                str(date_from),
                str(date_to),
                filters["carrier"],
                filters["product"],
                filters["lead_source"],
                filters["addon"],
                filters["team"],
                "sales" if include_sales else "",
            ]
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        payload = build_leaderboard_payload(
            date_from,
            date_to,
            filters=filters,
            include_sales=include_sales,
        )
        cache.set(cache_key, payload, 30)
        return Response(payload)


class AgentsListView(APIView):
    """Paginated agent directory with aggregated sales stats (Agents page)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if not (is_admin(user) or is_manager(user)):
            return Response({"detail": "Forbidden"}, status=403)

        search = request.query_params.get("search", "").strip()
        team = request.query_params.get("team", "all")
        try:
            page = max(1, int(request.query_params.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = min(100, max(1, int(request.query_params.get("page_size", 15))))
        except (TypeError, ValueError):
            page_size = 15

        qs = Profile.objects.select_related("team")
        if is_admin(user):
            pass
        else:
            managed = managed_team_ids(user)
            qs = qs.filter(Q(user_id=user.pk) | Q(team_id__in=managed))

        qs = qs.annotate(
            sales_count=Count("user__sales"),
            revenue=Coalesce(
                Sum("user__sales__deal_size"),
                Value(0),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            ),
        )

        if search:
            qs = qs.filter(display_name__icontains=search)
        if team != "all":
            if team == "none":
                qs = qs.filter(team_id__isnull=True)
            else:
                qs = qs.filter(team_id=team)

        total = qs.count()
        offset = (page - 1) * page_size
        profiles = qs.order_by("-revenue", "display_name")[offset : offset + page_size]

        def _num(value):
            return float(value) if value is not None else None

        data = [
            {
                "agent_id": str(p.user_id),
                "agent_name": p.display_name,
                "team_id": str(p.team_id) if p.team_id else None,
                "team_name": p.team.name if p.team else "Unassigned",
                "sales_count": p.sales_count,
                "revenue": _num(p.revenue),
            }
            for p in profiles
        ]

        return Response(
            {
                "data": data,
                "count": total,
                "page": page,
                "page_size": page_size,
            }
        )
