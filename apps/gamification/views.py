from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.dbapi.roles import is_admin
from apps.gamification.incentives import build_incentives_payload
from apps.gamification.models import ActivityEvent, AgentProgress, Redemption, Reward
from apps.gamification.points import InsufficientPointsError, request_redemption, review_redemption
from apps.gamification.services import progress_payload


class MyProgressView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        progress = AgentProgress.objects.filter(agent=request.user).select_related("level").first()
        if not progress:
            return Response(
                {
                    "agent_id": str(request.user.pk),
                    "total_xp": 0,
                    "level_rank": 0,
                    "level_slug": "unranked",
                    "level_name": "Rookie",
                    "level_tier": "level",
                    "xp_to_next": 2500,
                    "next_level_name": "Prospect",
                    "level_progress_pct": 0,
                    "current_streak": 0,
                    "best_streak": 0,
                    "points_balance": 0,
                }
            )
        return Response(progress_payload(progress))


class AllProgressView(APIView):
    """All agents' gamification progress (for leaderboard badges)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        progress_rows = AgentProgress.objects.select_related("level").all()
        by_agent = {str(p.agent_id): progress_payload(p) for p in progress_rows}
        return Response({"progress": by_agent})


class ActivityFeedView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            limit = min(50, max(1, int(request.query_params.get("limit", 20))))
        except (TypeError, ValueError):
            limit = 20

        events = ActivityEvent.objects.all()[:limit]
        data = [
            {
                "id": str(e.id),
                "event_type": e.event_type,
                "agent_id": str(e.agent_id) if e.agent_id else None,
                "agent_name": e.agent_name,
                "message": e.message,
                "metadata": e.metadata or {},
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ]
        return Response({"events": data})


class IncentivesView(APIView):
    """Personal incentives hub: levels, badges, contests, weekly challenges."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(build_incentives_payload(str(request.user.pk)))


class RedeemRewardView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, reward_id):
        note = (request.data.get("note") or "").strip()
        try:
            redemption = request_redemption(request.user.pk, reward_id, agent_note=note)
        except InsufficientPointsError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Reward.DoesNotExist:
            return Response({"detail": "Reward not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(
            {
                "id": str(redemption.id),
                "status": redemption.status,
                "points_cost": redemption.points_cost,
                "reward_name": redemption.reward.name,
            },
            status=status.HTTP_201_CREATED,
        )


class RedemptionReviewView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, redemption_id):
        if not is_admin(request.user):
            return Response({"detail": "Admin only."}, status=status.HTTP_403_FORBIDDEN)

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

        return Response(
            {
                "id": str(redemption.id),
                "status": redemption.status,
                "admin_note": redemption.admin_note,
            }
        )


class PendingRedemptionsView(APIView):
    """Admin queue of pending reward redemptions."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not is_admin(request.user):
            return Response({"detail": "Admin only."}, status=status.HTTP_403_FORBIDDEN)

        rows = (
            Redemption.objects.filter(status=Redemption.STATUS_PENDING)
            .select_related("reward", "agent")
            .order_by("created_at")[:50]
        )
        return Response(
            {
                "redemptions": [
                    {
                        "id": str(r.id),
                        "agent_id": str(r.agent_id),
                        "agent_email": getattr(r.agent, "email", ""),
                        "reward_name": r.reward.name,
                        "reward_icon": r.reward.icon,
                        "points_cost": r.points_cost,
                        "agent_note": r.agent_note,
                        "created_at": r.created_at.isoformat(),
                    }
                    for r in rows
                ]
            }
        )
