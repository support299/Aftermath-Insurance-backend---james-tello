import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def refresh_expiring_tokens():
    """Hourly refresh of the GHL company + location tokens (same behavior as
    the public ghl-refresh hook the original app exposed for cron)."""
    from django.conf import settings

    from apps.ghl import services
    from apps.ghl.models import GhlToken

    if not settings.GHL_CLIENT_SECRET:
        logger.info("GHL_CLIENT_SECRET not configured; skipping token refresh")
        return "skipped"

    company_row = GhlToken.objects.filter(location_id__isnull=True).first()
    if not company_row:
        return "no company token"

    token = services.post_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": company_row.refresh_token,
            "user_type": "Company",
        }
    )
    services.persist_token(token)
    services.exchange_and_store_location(token)
    logger.info("GHL tokens refreshed at %s", timezone.now().isoformat())
    return "ok"
