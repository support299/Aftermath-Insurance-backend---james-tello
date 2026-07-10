"""Manager-scoped contest APIs (team-only contests)."""

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
)
from apps.gamification.models import Contest
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
