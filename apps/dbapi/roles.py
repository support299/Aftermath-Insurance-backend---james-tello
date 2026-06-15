"""Role helpers mirroring the original Supabase SQL functions
(has_role, current_user_team, is_team_manager)."""

from apps.authentication.models import Profile, UserRole
from apps.teams.models import TeamManager


def get_roles(user) -> set[str]:
    if not user or not user.is_authenticated:
        return set()
    if not hasattr(user, "_cached_roles"):
        user._cached_roles = set(
            UserRole.objects.filter(user=user).values_list("role", flat=True)
        )
    return user._cached_roles


def has_role(user, role: str) -> bool:
    return role in get_roles(user)


def is_admin(user) -> bool:
    return has_role(user, "admin")


def is_manager(user) -> bool:
    return has_role(user, "manager")


def current_user_team(user):
    """Equivalent of the SQL current_user_team(): the user's profile team_id."""
    if not user or not user.is_authenticated:
        return None
    if not hasattr(user, "_cached_team_id"):
        user._cached_team_id = (
            Profile.objects.filter(pk=user.pk).values_list("team_id", flat=True).first()
        )
    return user._cached_team_id


def managed_team_ids(user) -> set:
    """Teams the user manages. Mirrors is_team_manager(): checks ONLY the
    team_managers table (teams.manager_id was backfilled into it)."""
    if not user or not user.is_authenticated:
        return set()
    if not hasattr(user, "_cached_managed_teams"):
        user._cached_managed_teams = set(
            TeamManager.objects.filter(user=user).values_list("team_id", flat=True)
        )
    return user._cached_managed_teams


def current_ghl_user_ids(user) -> list[str]:
    """Mirrors current_ghl_user_ids(): ghl_users.id where app_user_id = uid."""
    from apps.ghl.models import GhlUser

    if not user or not user.is_authenticated:
        return []
    return list(GhlUser.objects.filter(app_user=user).values_list("id", flat=True))
