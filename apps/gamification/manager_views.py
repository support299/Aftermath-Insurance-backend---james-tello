"""Manager-scoped contest + reward APIs (team-only)."""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.dbapi.roles import is_manager, managed_team_ids
from apps.gamification.admin_views import (
    _apply_contest_patch,
    _contest_payload,
    _parse_contest_fields,
    _redemption_payload,
    _reward_payload,
    _unique_slug,
)
from apps.gamification.models import Contest, Redemption, Reward
from apps.gamification.points import review_redemption
from apps.teams.models import Team


class ManagerRequiredMixin:
    def deny_unless_manager(self, request):
        if not is_manager(request.user):
            return Response({"detail": "Manager only."}, status=status.HTTP_403_FORBIDDEN)
        return None

    def managed_teams(self, user):
        return managed_team_ids(user)


class ManagerContestsView(ManagerRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if denied := self.deny_unless_manager(request):
            return denied
        managed = self.managed_teams(request.user)
        teams = list(Team.objects.filter(id__in=managed).order_by("name"))
        rows = (
            Contest.objects.filter(team_id__in=managed)
            .select_related("team")
            .order_by("-start_date")
        )
        return Response(
            {
                "contests": [_contest_payload(c) for c in rows],
                "teams": [{"id": str(t.id), "name": t.name} for t in teams],
            }
        )

    def post(self, request):
        if denied := self.deny_unless_manager(request):
            return denied
        managed = self.managed_teams(request.user)
        if not managed:
            return Response({"detail": "You do not manage any teams."}, status=status.HTTP_403_FORBIDDEN)

        fields, err = _parse_contest_fields(request, require_team=True)
        if err:
            return err

        team_id = str(fields["team_id"])
        if team_id not in {str(t) for t in managed}:
            return Response({"detail": "You can only create contests for teams you manage."}, status=status.HTTP_403_FORBIDDEN)

        contest = Contest.objects.create(
            title=fields["title"],
            description=fields["description"],
            prize_description=fields["prize_description"],
            metric=fields["metric"],
            target_value=fields["target_value"],
            start_date=fields["start_date"],
            end_date=fields["end_date"],
            is_active=fields["is_active"],
            team_id=team_id,
        )
        contest = Contest.objects.select_related("team").get(pk=contest.pk)
        return Response(_contest_payload(contest), status=status.HTTP_201_CREATED)


class ManagerContestDetailView(ManagerRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, contest_id):
        if denied := self.deny_unless_manager(request):
            return denied
        managed = self.managed_teams(request.user)
        try:
            contest = Contest.objects.select_related("team").get(pk=contest_id)
        except Contest.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if not contest.team_id or str(contest.team_id) not in {str(t) for t in managed}:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if patch_err := _apply_contest_patch(contest, request):
            return patch_err
        contest.save()
        return Response(_contest_payload(contest))


class ManagerRewardsView(ManagerRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if denied := self.deny_unless_manager(request):
            return denied
        managed = self.managed_teams(request.user)
        teams = list(Team.objects.filter(id__in=managed).order_by("name"))
        rows = (
            Reward.objects.filter(team_id__in=managed)
            .select_related("team")
            .order_by("sort_order", "points_cost")
        )
        return Response(
            {
                "rewards": [_reward_payload(r) for r in rows],
                "teams": [{"id": str(t.id), "name": t.name} for t in teams],
            }
        )

    def post(self, request):
        if denied := self.deny_unless_manager(request):
            return denied
        managed = self.managed_teams(request.user)
        if not managed:
            return Response({"detail": "You do not manage any teams."}, status=status.HTTP_403_FORBIDDEN)

        name = (request.data.get("name") or "").strip()
        if not name:
            return Response({"detail": "Name is required."}, status=status.HTTP_400_BAD_REQUEST)
        team_id = request.data.get("team_id")
        if not team_id:
            return Response({"detail": "Team is required."}, status=status.HTTP_400_BAD_REQUEST)
        if str(team_id) not in {str(t) for t in managed}:
            return Response(
                {"detail": "You can only create rewards for teams you manage."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            points_cost = int(request.data.get("points_cost", 0))
        except (TypeError, ValueError):
            return Response({"detail": "Invalid points cost."}, status=status.HTTP_400_BAD_REQUEST)
        if points_cost <= 0:
            return Response({"detail": "Points cost must be positive."}, status=status.HTTP_400_BAD_REQUEST)

        slug = _unique_slug(name, Reward)
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


class ManagerRewardDetailView(ManagerRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, reward_id):
        if denied := self.deny_unless_manager(request):
            return denied
        managed = self.managed_teams(request.user)
        try:
            reward = Reward.objects.select_related("team").get(pk=reward_id)
        except Reward.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if not reward.team_id or str(reward.team_id) not in {str(t) for t in managed}:
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
        # Managers cannot reassign reward to another team / make it global.
        reward.save()
        reward = Reward.objects.select_related("team").get(pk=reward.pk)
        return Response(_reward_payload(reward))


class ManagerRedemptionsView(ManagerRequiredMixin, APIView):
    """Team-reward redemptions for teams this manager owns."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if denied := self.deny_unless_manager(request):
            return denied
        managed = self.managed_teams(request.user)
        if not managed:
            return Response({"redemptions": []})

        status_filter = (request.query_params.get("status") or "pending").strip()
        qs = (
            Redemption.objects.select_related("reward", "reward__team", "agent", "agent__profile")
            .filter(reward__team_id__in=managed)
            .order_by("-created_at")
        )
        if status_filter != "all":
            qs = qs.filter(status=status_filter)
        return Response({"redemptions": [_redemption_payload(r) for r in qs[:100]]})


class ManagerRedemptionDetailView(ManagerRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, redemption_id):
        if denied := self.deny_unless_manager(request):
            return denied
        managed = self.managed_teams(request.user)
        try:
            existing = Redemption.objects.select_related("reward").get(pk=redemption_id)
        except Redemption.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if not existing.reward.team_id or str(existing.reward.team_id) not in {str(t) for t in managed}:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        new_status = (request.data.get("status") or "").strip()
        if new_status not in (
            Redemption.STATUS_APPROVED,
            Redemption.STATUS_REJECTED,
            Redemption.STATUS_FULFILLED,
        ):
            return Response({"detail": "Invalid status."}, status=status.HTTP_400_BAD_REQUEST)

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
