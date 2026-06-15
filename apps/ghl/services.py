"""GoHighLevel OAuth + API helpers, ported 1:1 from the original
src/lib/ghl.functions.ts server functions."""

import logging
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone

from apps.ghl.models import GhlToken

logger = logging.getLogger(__name__)

GHL_TOKEN_URL = "https://services.leadconnectorhq.com/oauth/token"
GHL_LOCATION_TOKEN_URL = "https://services.leadconnectorhq.com/oauth/locationToken"


class GhlError(Exception):
    pass


def post_token(params: dict) -> dict:
    body = {
        "client_id": settings.GHL_CLIENT_ID,
        "client_secret": settings.GHL_CLIENT_SECRET,
        **params,
    }
    res = requests.post(
        GHL_TOKEN_URL,
        data=body,
        headers={"Accept": "application/json"},
        timeout=30,
    )
    if not res.ok:
        raise GhlError(f"GHL token error {res.status_code}: {res.text}")
    return res.json()


def mint_location_token(company_access_token: str, company_id: str, location_id: str) -> dict:
    res = requests.post(
        GHL_LOCATION_TOKEN_URL,
        data={"companyId": company_id, "locationId": location_id},
        headers={
            "Accept": "application/json",
            "Version": "2021-07-28",
            "Authorization": f"Bearer {company_access_token}",
        },
        timeout=30,
    )
    if not res.ok:
        raise GhlError(f"GHL locationToken error {res.status_code}: {res.text}")
    return res.json()


def persist_token(token: dict) -> None:
    expires_at = timezone.now() + timedelta(seconds=token.get("expires_in", 0))
    location_id = token.get("locationId")
    row = {
        "access_token": token["access_token"],
        "refresh_token": token.get("refresh_token") or "",
        "expires_at": expires_at,
        "location_id": location_id,
        "company_id": token.get("companyId"),
        "user_type": token.get("userType"),
        "scope": token.get("scope"),
        "raw": token,
    }
    existing = (
        GhlToken.objects.filter(location_id=location_id).first()
        if location_id
        else GhlToken.objects.filter(location_id__isnull=True).first()
    )
    if existing:
        for k, v in row.items():
            setattr(existing, k, v)
        existing.save()
    else:
        GhlToken.objects.create(**row)


def exchange_and_store_location(company_token: dict) -> None:
    if not company_token.get("companyId"):
        return
    loc_token = mint_location_token(
        company_token["access_token"],
        company_token["companyId"],
        settings.GHL_LOCATION_ID,
    )
    loc_token.setdefault("locationId", settings.GHL_LOCATION_ID)
    loc_token.setdefault("companyId", company_token["companyId"])
    loc_token.setdefault("userType", "Location")
    persist_token(loc_token)


def get_valid_location_token() -> tuple[str, str]:
    row = (
        GhlToken.objects.filter(location_id__isnull=False)
        .order_by("-updated_at")
        .first()
    )
    if not row or not row.location_id:
        raise GhlError("No GHL location connection found.")
    if (row.expires_at - timezone.now()).total_seconds() > 60:
        return row.access_token, row.location_id
    refreshed = post_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": row.refresh_token,
            "user_type": "Location",
        }
    )
    refreshed.setdefault("locationId", row.location_id)
    if row.company_id:
        refreshed.setdefault("companyId", row.company_id)
    refreshed.setdefault("userType", "Location")
    persist_token(refreshed)
    return refreshed["access_token"], row.location_id


def get_location_access_token(location_id: str | None = None) -> str | None:
    qs = GhlToken.objects.filter(location_id__isnull=False)
    row = qs.filter(location_id=location_id).first() if location_id else qs.first()
    return row.access_token if row else None


def fetch_contact_assigned_user_id(contact_id: str, location_id: str | None) -> str | None:
    token = get_location_access_token(location_id)
    if not token:
        return None
    try:
        res = requests.get(
            f"https://services.leadconnectorhq.com/contacts/{contact_id}",
            headers={
                "Accept": "application/json",
                "Version": "2021-07-28",
                "Authorization": f"Bearer {token}",
            },
            timeout=30,
        )
        if not res.ok:
            logger.error("GHL contact fetch failed %s %s", res.status_code, res.text)
            return None
        return (res.json().get("contact") or {}).get("assignedTo")
    except requests.RequestException as e:
        logger.error("GHL contact fetch error %s", e)
        return None
