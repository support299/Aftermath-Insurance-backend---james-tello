from apps.dbapi.roles import is_admin, is_manager, managed_team_ids
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authentication.models import Profile
from apps.catalog.models import AddOn, Product
from apps.payouts.models import (
    AgentIncomeGoal,
    CompLevel,
    OnboardingMilestone,
    ProductCommission,
    TrackerConfig,
)
from apps.payouts.services import (
    agent_comp_level_code,
    agent_income_goal_payload,
    agent_milestones_payload,
    agent_tracker_payload,
    estimate_line_items_payout,
    evaluate_milestones_for_agent,
    list_comp_levels_payload,
)
from apps.sales.models import Sale


class AdminRequiredMixin:
    def deny_unless_admin(self, request):
        if not is_admin(request.user):
            return Response({"detail": "Admin only."}, status=403)
        return None


def _can_view_agent(user, agent_id) -> bool:
    if str(user.id) == str(agent_id):
        return True
    if is_admin(user):
        return True
    if is_manager(user):
        team_id = Profile.objects.filter(pk=agent_id).values_list("team_id", flat=True).first()
        return bool(team_id and team_id in managed_team_ids(user))
    return False


def _commission_payload(row: ProductCommission) -> dict:
    return {
        "id": str(row.id),
        "product_id": str(row.product_id) if row.product_id else None,
        "add_on_id": str(row.add_on_id) if row.add_on_id else None,
        "label": row.label
        or (row.product.name if row.product_id and row.product else None)
        or (row.add_on.name if row.add_on_id and row.add_on else "")
        or "",
        "product_name": row.product.name if row.product_id and row.product else None,
        "add_on_name": row.add_on.name if row.add_on_id and row.add_on else None,
        "carrier_name": (
            row.product.carrier.name
            if row.product_id and row.product and row.product.carrier_id
            else None
        ),
        "advance_months": row.advance_months,
        "rates": row.rates or {},
        "is_active": row.is_active,
    }


# ── Public (authenticated) ───────────────────────────────────────────────────


class CompLevelsListView(APIView):
    """Active levels — needed for admin UX; agents only see their own code via /me."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        active_only = request.query_params.get("all") != "1"
        qs = CompLevel.objects.order_by("sort_order", "name")
        if active_only:
            qs = qs.filter(is_active=True)
        if not is_admin(request.user) and request.query_params.get("all") == "1":
            return Response({"detail": "Admin only."}, status=403)
        return Response(
            {
                "levels": [
                    {
                        "id": str(lv.id),
                        "code": lv.code,
                        "name": lv.name,
                        "sort_order": lv.sort_order,
                        "is_active": lv.is_active,
                    }
                    for lv in qs
                ]
            }
        )


class MyCompLevelView(APIView):
    """Own comp level only — never leaks other agents' levels."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        profile = (
            Profile.objects.select_related("comp_level")
            .filter(pk=request.user.id)
            .first()
        )
        lv = profile.comp_level if profile else None
        return Response(
            {
                "comp_level_id": str(lv.id) if lv else None,
                "code": lv.code if lv else None,
                "name": lv.name if lv else None,
            }
        )


class EstimatePayoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        line_items = request.data.get("line_items") or []
        if not isinstance(line_items, list):
            return Response({"detail": "line_items must be a list."}, status=400)

        agent_id = request.data.get("agent_id") or str(request.user.id)
        if not _can_view_agent(request.user, agent_id):
            return Response({"detail": "Forbidden."}, status=403)

        level_code = request.data.get("level_code")
        if not level_code:
            level_code = agent_comp_level_code(agent_id)

        return Response(estimate_line_items_payout(line_items, level_code))


class AgentTrackerView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, agent_id=None):
        target = agent_id or request.user.id
        if not _can_view_agent(request.user, target):
            return Response({"detail": "Forbidden."}, status=403)
        return Response(agent_tracker_payload(target))


class AgentIncomeGoalView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, agent_id=None):
        target = agent_id or request.user.id
        if not _can_view_agent(request.user, target):
            return Response({"detail": "Forbidden."}, status=403)
        return Response(agent_income_goal_payload(target))

    def put(self, request, agent_id=None):
        target = agent_id or request.user.id
        # Agent can set own goal; admin/manager (same team) can set for others
        if str(request.user.id) != str(target) and not _can_view_agent(request.user, target):
            return Response({"detail": "Forbidden."}, status=403)
        if str(request.user.id) != str(target) and not (
            is_admin(request.user) or is_manager(request.user)
        ):
            return Response({"detail": "Forbidden."}, status=403)

        try:
            goal = float(request.data.get("annual_income_goal") or 0)
        except (TypeError, ValueError):
            return Response({"detail": "Invalid goal."}, status=400)
        if goal < 0:
            return Response({"detail": "Goal must be ≥ 0."}, status=400)

        row, _ = AgentIncomeGoal.objects.update_or_create(
            pk=target, defaults={"annual_income_goal": goal}
        )
        return Response(agent_income_goal_payload(target))


class AgentMilestonesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, agent_id=None):
        target = agent_id or request.user.id
        if not _can_view_agent(request.user, target):
            return Response({"detail": "Forbidden."}, status=403)
        return Response(agent_milestones_payload(target))


class AgentOnboardingDashboardView(APIView):
    """Bundled tracker + income goal + milestones + payout YTD for dashboard cards."""

    permission_classes = [IsAuthenticated]

    def get(self, request, agent_id=None):
        target = agent_id or request.user.id
        if not _can_view_agent(request.user, target):
            return Response({"detail": "Forbidden."}, status=403)
        return Response(
            {
                "tracker": agent_tracker_payload(target),
                "income_goal": agent_income_goal_payload(target),
                "milestones": agent_milestones_payload(target),
                "comp_level": {
                    "code": agent_comp_level_code(target),
                },
            }
        )


class CommissionsCatalogView(APIView):
    """Active commission table for client-side estimate while typing a sale."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = ProductCommission.objects.filter(is_active=True).select_related(
            "product", "product__carrier", "add_on"
        )
        levels = list_comp_levels_payload()
        my_code = agent_comp_level_code(request.user.id)
        return Response(
            {
                "levels": [lv for lv in levels if lv["is_active"]],
                "my_level_code": my_code,
                "commissions": [_commission_payload(r) for r in rows],
            }
        )


# ── Admin ────────────────────────────────────────────────────────────────────


class AdminCompLevelsView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if denied := self.deny_unless_admin(request):
            return denied
        return Response({"levels": list_comp_levels_payload()})

    def post(self, request):
        if denied := self.deny_unless_admin(request):
            return denied
        code = (request.data.get("code") or "").strip().upper().replace(" ", "_")
        name = (request.data.get("name") or "").strip()
        if not code or not name:
            return Response({"detail": "code and name required."}, status=400)
        if CompLevel.objects.filter(code__iexact=code).exists():
            return Response({"detail": "Code already exists."}, status=400)
        sort_order = int(request.data.get("sort_order") or 0)
        lv = CompLevel.objects.create(code=code, name=name, sort_order=sort_order)
        return Response(
            {
                "id": str(lv.id),
                "code": lv.code,
                "name": lv.name,
                "sort_order": lv.sort_order,
                "is_active": lv.is_active,
            },
            status=201,
        )


class AdminCompLevelDetailView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, level_id):
        if denied := self.deny_unless_admin(request):
            return denied
        try:
            lv = CompLevel.objects.get(pk=level_id)
        except CompLevel.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)
        if "name" in request.data:
            lv.name = (request.data["name"] or "").strip() or lv.name
        if "sort_order" in request.data:
            lv.sort_order = int(request.data["sort_order"] or 0)
        if "is_active" in request.data:
            lv.is_active = bool(request.data["is_active"])
        lv.save()
        return Response(
            {
                "id": str(lv.id),
                "code": lv.code,
                "name": lv.name,
                "sort_order": lv.sort_order,
                "is_active": lv.is_active,
            }
        )


class AdminCommissionsView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if denied := self.deny_unless_admin(request):
            return denied
        rows = ProductCommission.objects.select_related(
            "product", "product__carrier", "add_on"
        ).order_by("label")
        return Response(
            {
                "commissions": [_commission_payload(r) for r in rows],
                "levels": list_comp_levels_payload(),
            }
        )

    def post(self, request):
        if denied := self.deny_unless_admin(request):
            return denied
        product_id = request.data.get("product_id")
        add_on_id = request.data.get("add_on_id")
        if bool(product_id) == bool(add_on_id):
            return Response(
                {"detail": "Provide exactly one of product_id or add_on_id."},
                status=400,
            )
        if product_id and not Product.objects.filter(pk=product_id).exists():
            return Response({"detail": "Product not found."}, status=404)
        if add_on_id and not AddOn.objects.filter(pk=add_on_id).exists():
            return Response({"detail": "Add-on not found."}, status=404)
        if product_id and ProductCommission.objects.filter(product_id=product_id).exists():
            return Response({"detail": "Commission already exists for product."}, status=400)
        if add_on_id and ProductCommission.objects.filter(add_on_id=add_on_id).exists():
            return Response({"detail": "Commission already exists for add-on."}, status=400)

        months = int(request.data.get("advance_months") or 6)
        rates = request.data.get("rates") or {}
        if not isinstance(rates, dict):
            return Response({"detail": "rates must be an object."}, status=400)

        label = (request.data.get("label") or "").strip()
        row = ProductCommission.objects.create(
            product_id=product_id,
            add_on_id=add_on_id,
            label=label,
            advance_months=months,
            rates=rates,
            is_active=bool(request.data.get("is_active", True)),
        )
        row = ProductCommission.objects.select_related(
            "product", "product__carrier", "add_on"
        ).get(pk=row.pk)
        return Response(_commission_payload(row), status=201)


class AdminCommissionDetailView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, commission_id):
        if denied := self.deny_unless_admin(request):
            return denied
        try:
            row = ProductCommission.objects.select_related(
                "product", "product__carrier", "add_on"
            ).get(pk=commission_id)
        except ProductCommission.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)

        if "advance_months" in request.data:
            row.advance_months = int(request.data["advance_months"] or 6)
        if "rates" in request.data:
            if not isinstance(request.data["rates"], dict):
                return Response({"detail": "rates must be an object."}, status=400)
            row.rates = request.data["rates"]
        if "label" in request.data:
            row.label = (request.data["label"] or "").strip()
        if "is_active" in request.data:
            row.is_active = bool(request.data["is_active"])
        row.save()
        return Response(_commission_payload(row))

    def delete(self, request, commission_id):
        if denied := self.deny_unless_admin(request):
            return denied
        try:
            row = ProductCommission.objects.get(pk=commission_id)
        except ProductCommission.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)
        row.is_active = False
        row.save(update_fields=["is_active", "updated_at"])
        return Response({"ok": True})


class AdminSetAgentCompLevelView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, agent_id):
        if denied := self.deny_unless_admin(request):
            return denied
        try:
            profile = Profile.objects.get(pk=agent_id)
        except Profile.DoesNotExist:
            return Response({"detail": "Profile not found."}, status=404)

        level_id = request.data.get("comp_level_id")
        if level_id in ("", None):
            profile.comp_level = None
            profile.save(update_fields=["comp_level", "updated_at"])
            return Response({"comp_level_id": None, "code": None, "name": None})

        try:
            lv = CompLevel.objects.get(pk=level_id)
        except CompLevel.DoesNotExist:
            return Response({"detail": "Comp level not found."}, status=404)
        profile.comp_level = lv
        profile.save(update_fields=["comp_level", "updated_at"])
        return Response(
            {"comp_level_id": str(lv.id), "code": lv.code, "name": lv.name}
        )


class AdminTrackerConfigView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if denied := self.deny_unless_admin(request):
            return denied
        cfg = TrackerConfig.load()
        return Response(
            {
                "foundation_weeks": cfg.foundation_weeks,
                "beacon_weeks": cfg.beacon_weeks,
                "phase_goal": float(cfg.phase_goal),
                "blended_income_rate": float(cfg.blended_income_rate),
            }
        )

    def patch(self, request):
        if denied := self.deny_unless_admin(request):
            return denied
        cfg = TrackerConfig.load()
        if "foundation_weeks" in request.data:
            cfg.foundation_weeks = int(request.data["foundation_weeks"] or 13)
        if "beacon_weeks" in request.data:
            cfg.beacon_weeks = int(request.data["beacon_weeks"] or 13)
        if "phase_goal" in request.data:
            cfg.phase_goal = request.data["phase_goal"] or 250000
        if "blended_income_rate" in request.data:
            cfg.blended_income_rate = request.data["blended_income_rate"] or 0.15
        cfg.save()
        return self.get(request)


class AdminMilestonesView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if denied := self.deny_unless_admin(request):
            return denied
        rows = OnboardingMilestone.objects.order_by("sort_order", "name")
        return Response(
            {
                "milestones": [
                    {
                        "id": str(m.id),
                        "slug": m.slug,
                        "name": m.name,
                        "description": m.description,
                        "milestone_type": m.milestone_type,
                        "threshold": float(m.threshold),
                        "cash_reward": float(m.cash_reward),
                        "sort_order": m.sort_order,
                        "is_active": m.is_active,
                    }
                    for m in rows
                ]
            }
        )

    def post(self, request):
        if denied := self.deny_unless_admin(request):
            return denied
        slug = (request.data.get("slug") or "").strip().lower().replace(" ", "-")
        name = (request.data.get("name") or "").strip()
        mtype = request.data.get("milestone_type") or OnboardingMilestone.TYPE_SALE_COUNT
        if not slug or not name:
            return Response({"detail": "slug and name required."}, status=400)
        m = OnboardingMilestone.objects.create(
            slug=slug,
            name=name,
            description=(request.data.get("description") or "").strip(),
            milestone_type=mtype,
            threshold=request.data.get("threshold") or 1,
            cash_reward=request.data.get("cash_reward") or 0,
            sort_order=int(request.data.get("sort_order") or 0),
            is_active=bool(request.data.get("is_active", True)),
        )
        return Response(
            {
                "id": str(m.id),
                "slug": m.slug,
                "name": m.name,
                "description": m.description,
                "milestone_type": m.milestone_type,
                "threshold": float(m.threshold),
                "cash_reward": float(m.cash_reward),
                "sort_order": m.sort_order,
                "is_active": m.is_active,
            },
            status=201,
        )


class AdminMilestoneDetailView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, milestone_id):
        if denied := self.deny_unless_admin(request):
            return denied
        try:
            m = OnboardingMilestone.objects.get(pk=milestone_id)
        except OnboardingMilestone.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)
        for field in ("name", "description", "milestone_type"):
            if field in request.data:
                setattr(m, field, request.data[field])
        if "threshold" in request.data:
            m.threshold = request.data["threshold"]
        if "cash_reward" in request.data:
            m.cash_reward = request.data["cash_reward"]
        if "sort_order" in request.data:
            m.sort_order = int(request.data["sort_order"] or 0)
        if "is_active" in request.data:
            m.is_active = bool(request.data["is_active"])
        m.save()
        return Response(
            {
                "id": str(m.id),
                "slug": m.slug,
                "name": m.name,
                "description": m.description,
                "milestone_type": m.milestone_type,
                "threshold": float(m.threshold),
                "cash_reward": float(m.cash_reward),
                "sort_order": m.sort_order,
                "is_active": m.is_active,
            }
        )


class RecalcSalePayoutView(APIView):
    """Recalculate + persist estimated_payout on a sale; evaluate milestones."""

    permission_classes = [IsAuthenticated]

    def post(self, request, sale_id):
        try:
            sale = Sale.objects.get(pk=sale_id)
        except Sale.DoesNotExist:
            try:
                sale = Sale.objects.get(sale_id=sale_id)
            except Sale.DoesNotExist:
                return Response({"detail": "Sale not found."}, status=404)

        if not _can_view_agent(request.user, sale.agent_id):
            return Response({"detail": "Forbidden."}, status=403)

        level_code = agent_comp_level_code(sale.agent_id)
        result = estimate_line_items_payout(sale.line_items or [], level_code)
        sale.estimated_payout = result["estimated_payout"]
        sale.save(update_fields=["estimated_payout"])

        newly = evaluate_milestones_for_agent(sale.agent_id, triggering_sale=sale)
        return Response({**result, "sale_id": str(sale.id), "new_milestones": newly})
