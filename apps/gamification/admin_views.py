"""Admin-only gamification management APIs for the in-app Settings UI."""

from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation

from django.utils.dateparse import parse_date
from django.utils.text import slugify
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.dbapi.roles import is_admin
from apps.gamification.achievements import badge_meta_for_slug, evaluate_all_achievements
from apps.gamification.config import (
    DEFAULT_EARNING_RULES,
    DEFAULT_WEEKLY_CHALLENGES,
    earning_rules_payload,
    weekly_challenges_payload,
)
from apps.gamification.models import (
    AchievementDefinition,
    AgentProgress,
    Contest,
    GamificationConfig,
    LevelDefinition,
    Redemption,
    Reward,
)
from apps.gamification.points import admin_adjust_bonus_xp, admin_adjust_points, review_redemption
from apps.gamification.services import level_for_xp, progress_payload, recalculate_agent
from apps.authentication.models import Profile


class AdminRequiredMixin:
    def deny_unless_admin(self, request):
        if not is_admin(request.user):
            return Response({"detail": "Admin only."}, status=status.HTTP_403_FORBIDDEN)
        return None


def _unique_slug(base: str, model) -> str:
    slug = slugify(base)[:48] or "item"
    candidate = slug
    n = 2
    while model.objects.filter(slug=candidate).exists():
        candidate = f"{slug}-{n}"
        n += 1
    return candidate


def _reward_payload(r: Reward) -> dict:
    return {
        "id": str(r.id),
        "slug": r.slug,
        "name": r.name,
        "description": r.description,
        "icon": r.icon,
        "points_cost": r.points_cost,
        "sort_order": r.sort_order,
        "is_active": r.is_active,
        "team_id": str(r.team_id) if r.team_id else None,
        "team_name": r.team.name if getattr(r, "team", None) else None,
        "scope": "team" if r.team_id else "global",
        "created_at": r.created_at.isoformat(),
    }


def _date_iso(value) -> str:
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return str(value)


def _parse_contest_date(raw, label: str) -> tuple[datetime.date | None, Response | None]:
    if isinstance(raw, datetime.date):
        return raw, None
    if isinstance(raw, str):
        parsed = parse_date(raw.strip())
        if parsed:
            return parsed, None
    return None, Response({"detail": f"Invalid {label}."}, status=status.HTTP_400_BAD_REQUEST)


def _contest_payload(c: Contest) -> dict:
    return {
        "id": str(c.id),
        "title": c.title,
        "description": c.description,
        "prize_description": c.prize_description,
        "metric": c.metric,
        "target_value": float(c.target_value) if c.target_value is not None else None,
        "start_date": _date_iso(c.start_date),
        "end_date": _date_iso(c.end_date),
        "is_active": c.is_active,
        "team_id": str(c.team_id) if c.team_id else None,
        "team_name": c.team.name if getattr(c, "team", None) else None,
        "scope": "team" if c.team_id else "global",
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
    }


def _parse_contest_fields(request, *, require_team: bool = False) -> tuple[dict | None, Response | None]:
    title = (request.data.get("title") or "").strip()
    if not title:
        return None, Response({"detail": "Title is required."}, status=status.HTTP_400_BAD_REQUEST)
    start_date = request.data.get("start_date")
    end_date = request.data.get("end_date")
    if not start_date or not end_date:
        return None, Response({"detail": "Start and end dates are required."}, status=status.HTTP_400_BAD_REQUEST)

    start_date, start_err = _parse_contest_date(start_date, "start date")
    if start_err:
        return None, start_err
    end_date, end_err = _parse_contest_date(end_date, "end date")
    if end_err:
        return None, end_err

    metric = request.data.get("metric") or Contest.METRIC_REVENUE
    valid_metrics = {
        Contest.METRIC_REVENUE,
        Contest.METRIC_SALE_COUNT,
        Contest.METRIC_LIFE_REVENUE,
        Contest.METRIC_ADDON_REVENUE,
    }
    if metric not in valid_metrics:
        return None, Response({"detail": "Invalid metric."}, status=status.HTTP_400_BAD_REQUEST)

    target_value = None
    if request.data.get("target_value") not in (None, ""):
        try:
            target_value = Decimal(str(request.data["target_value"]))
        except (InvalidOperation, TypeError, ValueError):
            return None, Response({"detail": "Invalid target value."}, status=status.HTTP_400_BAD_REQUEST)

    team_id = request.data.get("team_id")
    if require_team and not team_id:
        return None, Response({"detail": "Team is required."}, status=status.HTTP_400_BAD_REQUEST)

    return {
        "title": title,
        "description": (request.data.get("description") or "").strip(),
        "prize_description": (request.data.get("prize_description") or "").strip(),
        "metric": metric,
        "target_value": target_value,
        "start_date": start_date,
        "end_date": end_date,
        "is_active": bool(request.data.get("is_active", True)),
        "team_id": team_id,
    }, None


def _apply_contest_patch(contest: Contest, request) -> Response | None:
    if "title" in request.data:
        contest.title = (request.data["title"] or "").strip() or contest.title
    if "description" in request.data:
        contest.description = (request.data["description"] or "").strip()
    if "prize_description" in request.data:
        contest.prize_description = (request.data["prize_description"] or "").strip()
    if "metric" in request.data:
        contest.metric = request.data["metric"]
    if "target_value" in request.data:
        if request.data["target_value"] in (None, ""):
            contest.target_value = None
        else:
            try:
                contest.target_value = Decimal(str(request.data["target_value"]))
            except (InvalidOperation, TypeError, ValueError):
                return Response({"detail": "Invalid target value."}, status=status.HTTP_400_BAD_REQUEST)
    if "start_date" in request.data:
        parsed, err = _parse_contest_date(request.data["start_date"], "start date")
        if err:
            return err
        contest.start_date = parsed
    if "end_date" in request.data:
        parsed, err = _parse_contest_date(request.data["end_date"], "end date")
        if err:
            return err
        contest.end_date = parsed
    if "is_active" in request.data:
        contest.is_active = bool(request.data["is_active"])
    return None


def _achievement_payload(a: AchievementDefinition) -> dict:
    meta = badge_meta_for_slug(a.slug)
    return {
        "id": str(a.id),
        "slug": a.slug,
        "name": a.name,
        "description": a.description,
        "icon": a.icon,
        "metric": a.metric,
        "period": a.period,
        "threshold": a.threshold,
        "sort_order": a.sort_order,
        "is_active": a.is_active,
        "points_reward": a.points_reward,
        "rule": meta["rule"],
        "trigger": meta["trigger"],
        "trigger_label": meta["trigger_label"],
        "display_hint": meta["display_hint"],
        "implemented": meta["implemented"],
    }


def _redemption_payload(r: Redemption) -> dict:
    reward = r.reward
    agent_name = ""
    profile = getattr(r.agent, "profile", None)
    if profile is not None:
        agent_name = profile.display_name or ""
    return {
        "id": str(r.id),
        "agent_id": str(r.agent_id),
        "agent_email": getattr(r.agent, "email", ""),
        "agent_name": agent_name,
        "reward_id": str(r.reward_id),
        "reward_name": reward.name,
        "reward_icon": reward.icon,
        "points_cost": r.points_cost,
        "status": r.status,
        "agent_note": r.agent_note,
        "admin_note": r.admin_note,
        "team_id": str(reward.team_id) if reward.team_id else None,
        "team_name": reward.team.name if getattr(reward, "team", None) else None,
        "scope": "team" if reward.team_id else "global",
        "created_at": r.created_at.isoformat(),
        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
    }


class AdminRewardsView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if denied := self.deny_unless_admin(request):
            return denied
        rows = Reward.objects.select_related("team").order_by("sort_order", "points_cost")
        return Response({"rewards": [_reward_payload(r) for r in rows]})

    def post(self, request):
        if denied := self.deny_unless_admin(request):
            return denied
        name = (request.data.get("name") or "").strip()
        if not name:
            return Response({"detail": "Name is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            points_cost = int(request.data.get("points_cost", 0))
        except (TypeError, ValueError):
            return Response({"detail": "Invalid points cost."}, status=status.HTTP_400_BAD_REQUEST)
        if points_cost <= 0:
            return Response({"detail": "Points cost must be positive."}, status=status.HTTP_400_BAD_REQUEST)

        slug = (request.data.get("slug") or "").strip() or _unique_slug(name, Reward)
        if Reward.objects.filter(slug=slug).exists():
            return Response({"detail": "Slug already exists."}, status=status.HTTP_400_BAD_REQUEST)

        # Admin-created rewards are company-wide unless team_id is explicitly set.
        team_id = request.data.get("team_id") or None
        reward = Reward.objects.create(
            slug=slug,
            name=name,
            description=(request.data.get("description") or "").strip(),
            icon=(request.data.get("icon") or "🎁").strip()[:8] or "🎁",
            points_cost=points_cost,
            sort_order=int(request.data.get("sort_order") or 0),
            is_active=bool(request.data.get("is_active", True)),
            team_id=team_id,
        )
        reward = Reward.objects.select_related("team").get(pk=reward.pk)
        return Response(_reward_payload(reward), status=status.HTTP_201_CREATED)


class AdminRewardDetailView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, reward_id):
        if denied := self.deny_unless_admin(request):
            return denied
        try:
            reward = Reward.objects.select_related("team").get(pk=reward_id)
        except Reward.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if "name" in request.data:
            reward.name = (request.data["name"] or "").strip() or reward.name
        if "description" in request.data:
            reward.description = (request.data["description"] or "").strip()
        if "icon" in request.data:
            reward.icon = (request.data["icon"] or "🎁").strip()[:8] or "🎁"
        if "points_cost" in request.data:
            try:
                pts = int(request.data["points_cost"])
                if pts <= 0:
                    raise ValueError
                reward.points_cost = pts
            except (TypeError, ValueError):
                return Response({"detail": "Invalid points cost."}, status=status.HTTP_400_BAD_REQUEST)
        if "sort_order" in request.data:
            reward.sort_order = int(request.data["sort_order"] or 0)
        if "is_active" in request.data:
            reward.is_active = bool(request.data["is_active"])
        reward.save()
        reward = Reward.objects.select_related("team").get(pk=reward.pk)
        return Response(_reward_payload(reward))

    def delete(self, request, reward_id):
        if denied := self.deny_unless_admin(request):
            return denied
        try:
            reward = Reward.objects.get(pk=reward_id)
        except Reward.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        reward.is_active = False
        reward.save(update_fields=["is_active"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminContestsView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if denied := self.deny_unless_admin(request):
            return denied
        rows = Contest.objects.select_related("team").order_by("-start_date")
        return Response({"contests": [_contest_payload(c) for c in rows]})

    def post(self, request):
        if denied := self.deny_unless_admin(request):
            return denied
        fields, err = _parse_contest_fields(request)
        if err:
            return err

        contest = Contest.objects.create(
            title=fields["title"],
            description=fields["description"],
            prize_description=fields["prize_description"],
            metric=fields["metric"],
            target_value=fields["target_value"],
            start_date=fields["start_date"],
            end_date=fields["end_date"],
            is_active=fields["is_active"],
            team_id=None,
        )
        return Response(_contest_payload(contest), status=status.HTTP_201_CREATED)


class AdminContestDetailView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, contest_id):
        if denied := self.deny_unless_admin(request):
            return denied
        try:
            contest = Contest.objects.select_related("team").get(pk=contest_id)
        except Contest.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if patch_err := _apply_contest_patch(contest, request):
            return patch_err
        contest.save()
        return Response(_contest_payload(contest))


class AdminAchievementsView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if denied := self.deny_unless_admin(request):
            return denied
        rows = AchievementDefinition.objects.order_by("sort_order", "name")
        return Response({"achievements": [_achievement_payload(a) for a in rows]})


class AdminAchievementDetailView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, achievement_id):
        if denied := self.deny_unless_admin(request):
            return denied
        try:
            ach = AchievementDefinition.objects.get(pk=achievement_id)
        except AchievementDefinition.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if "name" in request.data:
            ach.name = (request.data["name"] or "").strip() or ach.name
        if "description" in request.data:
            ach.description = (request.data["description"] or "").strip()
        if "icon" in request.data:
            ach.icon = (request.data["icon"] or "🏆").strip()[:8] or "🏆"
        if "threshold" in request.data:
            if request.data["threshold"] in (None, ""):
                ach.threshold = None
            else:
                ach.threshold = int(request.data["threshold"])
        if "sort_order" in request.data:
            ach.sort_order = int(request.data["sort_order"] or 0)
        if "is_active" in request.data:
            ach.is_active = bool(request.data["is_active"])
        if "points_reward" in request.data:
            if request.data["points_reward"] in (None, ""):
                ach.points_reward = None
            else:
                ach.points_reward = max(0, int(request.data["points_reward"]))
        ach.save()
        return Response(_achievement_payload(ach))


class AdminRedemptionsListView(AdminRequiredMixin, APIView):
    """Company-wide reward redemptions only. Team rewards are reviewed by managers."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if denied := self.deny_unless_admin(request):
            return denied
        status_filter = (request.query_params.get("status") or "pending").strip()
        qs = (
            Redemption.objects.select_related("reward", "reward__team", "agent", "agent__profile")
            .filter(reward__team__isnull=True)
            .order_by("-created_at")
        )
        if status_filter != "all":
            qs = qs.filter(status=status_filter)
        rows = qs[:100]
        return Response({"redemptions": [_redemption_payload(r) for r in rows]})


class AdminRedemptionDetailView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, redemption_id):
        if denied := self.deny_unless_admin(request):
            return denied
        new_status = (request.data.get("status") or "").strip()
        if new_status not in (
            Redemption.STATUS_APPROVED,
            Redemption.STATUS_REJECTED,
            Redemption.STATUS_FULFILLED,
        ):
            return Response({"detail": "Invalid status."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            existing = Redemption.objects.select_related("reward").get(pk=redemption_id)
        except Redemption.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if existing.reward.team_id:
            return Response(
                {"detail": "Team reward redemptions are approved by the team manager."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            redemption = review_redemption(
                redemption_id,
                request.user.pk,
                new_status,
                admin_note=(request.data.get("admin_note") or "").strip(),
            )
        except Redemption.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        redemption = (
            Redemption.objects.select_related("reward", "reward__team", "agent", "agent__profile")
            .get(pk=redemption.pk)
        )
        return Response(_redemption_payload(redemption))


class AdminEvaluateAchievementsView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if denied := self.deny_unless_admin(request):
            return denied
        import time

        include_monthly = bool(request.data.get("monthly"))
        include_lifetime = bool(request.data.get("lifetime"))
        started = time.perf_counter()
        awarded = evaluate_all_achievements(
            include_monthly=include_monthly,
            include_lifetime=include_lifetime,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return Response({"awarded": awarded, "elapsed_ms": elapsed_ms})


def _level_payload(lv: LevelDefinition) -> dict:
    return {
        "id": str(lv.id),
        "rank": lv.rank,
        "slug": lv.slug,
        "name": lv.name,
        "xp_required": lv.xp_required,
        "tier_type": lv.tier_type,
        "description": lv.description,
    }


class AdminGamificationConfigView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if denied := self.deny_unless_admin(request):
            return denied
        GamificationConfig.load()
        return Response(
            {
                "earning_rules": earning_rules_payload(),
                "weekly_challenges": weekly_challenges_payload(),
            }
        )

    def patch(self, request):
        if denied := self.deny_unless_admin(request):
            return denied
        cfg = GamificationConfig.load()
        if "earning_rules" in request.data and isinstance(request.data["earning_rules"], dict):
            merged = dict(cfg.earning_rules or DEFAULT_EARNING_RULES)
            for key in DEFAULT_EARNING_RULES:
                if key in request.data["earning_rules"]:
                    merged[key] = request.data["earning_rules"][key]
            cfg.earning_rules = merged
        if "weekly_challenges" in request.data and isinstance(request.data["weekly_challenges"], list):
            cfg.weekly_challenges = request.data["weekly_challenges"]
        cfg.save()
        return Response(
            {
                "earning_rules": earning_rules_payload(),
                "weekly_challenges": weekly_challenges_payload(),
            }
        )


class AdminLevelsView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if denied := self.deny_unless_admin(request):
            return denied
        rows = LevelDefinition.objects.order_by("rank")
        return Response({"levels": [_level_payload(lv) for lv in rows]})


class AdminLevelDetailView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, level_id):
        if denied := self.deny_unless_admin(request):
            return denied
        try:
            lv = LevelDefinition.objects.get(pk=level_id)
        except LevelDefinition.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if "name" in request.data:
            lv.name = (request.data["name"] or "").strip() or lv.name
        if "description" in request.data:
            lv.description = (request.data["description"] or "").strip()
        if "xp_required" in request.data:
            lv.xp_required = max(0, int(request.data["xp_required"]))
        lv.save()
        return Response(_level_payload(lv))


class AdminAgentProgressListView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if denied := self.deny_unless_admin(request):
            return denied
        search = (request.query_params.get("search") or "").strip()
        try:
            page = max(1, int(request.query_params.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = min(50, max(1, int(request.query_params.get("page_size", 25))))
        except (TypeError, ValueError):
            page_size = 25

        qs = Profile.objects.select_related("team").order_by("display_name")
        if search:
            qs = qs.filter(display_name__icontains=search)

        total = qs.count()
        offset = (page - 1) * page_size
        profiles = list(qs[offset : offset + page_size])
        agent_ids = [p.user_id for p in profiles]

        levels = list(LevelDefinition.objects.order_by("rank"))
        progress_map = {
            str(p.agent_id): p
            for p in AgentProgress.objects.select_related("level").filter(agent_id__in=agent_ids)
        }

        rows = []
        for profile in profiles:
            prog = progress_map.get(str(profile.user_id))
            if prog:
                current = prog.level or level_for_xp(prog.total_xp, levels)
                bonus = int(prog.bonus_xp)
                total_xp = int(prog.total_xp)
                points_balance = int(prog.points_balance)
            else:
                current = level_for_xp(0, levels)
                bonus = 0
                total_xp = 0
                points_balance = 0
            rows.append(
                {
                    "agent_id": str(profile.user_id),
                    "display_name": profile.display_name,
                    "email": profile.email,
                    "team_name": profile.team.name if profile.team else None,
                    "total_xp": total_xp,
                    "sales_xp": max(0, total_xp - bonus),
                    "bonus_xp": bonus,
                    "points_balance": points_balance,
                    "level_name": current.name if current else "Unranked",
                    "level_rank": current.rank if current else 0,
                    "level_tier": current.tier_type if current else "level",
                }
            )
        return Response(
            {
                "agents": rows,
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        )


class AdminAgentAdjustPointsView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, agent_id):
        if denied := self.deny_unless_admin(request):
            return denied
        try:
            amount = int(request.data.get("amount", 0))
        except (TypeError, ValueError):
            return Response({"detail": "Invalid amount."}, status=status.HTTP_400_BAD_REQUEST)
        if amount == 0:
            return Response({"detail": "Amount cannot be zero."}, status=status.HTTP_400_BAD_REQUEST)
        note = (request.data.get("note") or "").strip()
        try:
            admin_adjust_points(agent_id, amount, note)
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        prog = AgentProgress.objects.filter(agent_id=agent_id).first()
        return Response({"points_balance": prog.points_balance if prog else 0})


class AdminAgentAdjustXpView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, agent_id):
        if denied := self.deny_unless_admin(request):
            return denied
        try:
            delta = int(request.data.get("amount", 0))
        except (TypeError, ValueError):
            return Response({"detail": "Invalid amount."}, status=status.HTTP_400_BAD_REQUEST)
        if delta == 0:
            return Response({"detail": "Amount cannot be zero."}, status=status.HTTP_400_BAD_REQUEST)
        prog = admin_adjust_bonus_xp(agent_id, delta)
        return Response(progress_payload(prog))


class AdminRecalculateAllView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if denied := self.deny_unless_admin(request):
            return denied
        agent_ids = Profile.objects.values_list("user_id", flat=True)
        count = 0
        for aid in agent_ids:
            recalculate_agent(aid)
            count += 1
        return Response({"recalculated": count})
