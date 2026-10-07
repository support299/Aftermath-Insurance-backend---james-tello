"""Push dashboard teams and agents into Academy onboarding."""

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.db import connection, transaction

from apps.authentication.models import AppRole, Profile
from apps.teams.models import Team

logger = logging.getLogger(__name__)


def build_snapshot():
    teams = [
        {
            'id': str(team.id),
            'name': team.name,
            'active': True,
            'created_at': team.created_at.date().isoformat(),
        }
        for team in Team.objects.all().order_by('name')
    ]
    agents = []
    profiles = (
        Profile.objects.filter(user__roles__role=AppRole.AGENT)
        .select_related('user', 'team')
        .distinct()
    )
    for profile in profiles:
        email = (profile.email or getattr(profile.user, 'email', '') or '').strip()
        agents.append(
            {
                'user_id': str(profile.user_id),
                'name': profile.display_name,
                'email': email,
                'team_id': str(profile.team_id) if profile.team_id else None,
            }
        )
    return {'teams': teams, 'agents': agents}


def push_snapshot():
    url = getattr(settings, 'ACADEMY_SYNC_URL', '') or ''
    token = getattr(settings, 'ACADEMY_SYNC_TOKEN', '') or ''
    if not url or not token:
        raise RuntimeError('ACADEMY_SYNC_URL and ACADEMY_SYNC_TOKEN are required')
    body = json.dumps(build_snapshot()).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            'Content-Type': 'application/json',
            'X-Sync-Token': token,
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors='replace')
        raise RuntimeError(f'Academy sync failed ({exc.code}): {detail}') from exc


def schedule_academy_push():
    """Send one snapshot after the current transaction commits."""
    if not (getattr(settings, 'ACADEMY_SYNC_URL', '') and getattr(settings, 'ACADEMY_SYNC_TOKEN', '')):
        return
    if getattr(connection, '_academy_sync_scheduled', False):
        return
    connection._academy_sync_scheduled = True

    def _run():
        connection._academy_sync_scheduled = False
        try:
            push_snapshot()
        except Exception:
            logger.exception('Academy team sync failed')

    transaction.on_commit(_run)
