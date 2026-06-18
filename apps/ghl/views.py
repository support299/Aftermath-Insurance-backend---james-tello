"""GHL endpoints: admin OAuth management, contact sync from sales, and the
public webhook + token-refresh hooks. Logic ported 1:1 from the original
TanStack server functions/routes."""

import logging

import requests
from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authentication.models import Profile, User, UserRole
from apps.dbapi.roles import is_admin
from apps.dbapi.views import json_value
from apps.ghl import services
from apps.ghl.models import GhlContact, GhlToken, GhlUser, GhlWebhookLog

logger = logging.getLogger(__name__)

DEFAULT_GHL_PASSWORD = "P!nnacl3Adm!n#W3lln3ss"


def serialize_token(row: GhlToken | None) -> dict | None:
    if row is None:
        return None
    return {
        "access_token": row.access_token,
        "refresh_token": row.refresh_token,
        "expires_at": json_value(row.expires_at),
        "location_id": row.location_id,
        "company_id": row.company_id,
        "user_type": row.user_type,
        "scope": row.scope,
        "updated_at": json_value(row.updated_at),
    }


class AdminRequiredMixin:
    def check_admin(self, request):
        if not is_admin(request.user):
            return Response(
                {"error": "Forbidden: admin role required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        return None


class GhlOAuthConfigView(AdminRequiredMixin, APIView):
    """OAuth onboarding parameters for the /connect admin page."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        forbidden = self.check_admin(request)
        if forbidden:
            return forbidden
        return Response(
            {
                "client_id": settings.GHL_CLIENT_ID,
                "scopes": settings.GHL_SCOPES,
                "version_id": settings.GHL_VERSION_ID,
                "redirect_uri": settings.GHL_REDIRECT_URI,
            }
        )


class GhlStatusView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        forbidden = self.check_admin(request)
        if forbidden:
            return forbidden
        rows = list(GhlToken.objects.order_by("location_id"))
        company = next((r for r in rows if r.location_id is None), None)
        location = next((r for r in rows if r.location_id is not None), None)
        return Response(
            {
                "token": serialize_token(company),
                "locationToken": serialize_token(location),
                "tokens": [serialize_token(r) for r in rows],
            }
        )


class ExchangeCodeView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        forbidden = self.check_admin(request)
        if forbidden:
            return forbidden
        if not settings.GHL_CLIENT_SECRET:
            return Response({"error": "GHL_CLIENT_SECRET not configured"}, status=500)
        code = request.data.get("code")
        if not code:
            return Response({"error": "Missing code"}, status=400)
        try:
            token = services.post_token(
                {"grant_type": "authorization_code", "code": code, "user_type": "Location"}
            )
            services.persist_token(token)
            services.exchange_and_store_location(token)
        except services.GhlError as e:
            return Response({"error": str(e)}, status=502)
        return Response({"success": True})


class RefreshTokenView(AdminRequiredMixin, APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        forbidden = self.check_admin(request)
        if forbidden:
            return forbidden
        if not settings.GHL_CLIENT_SECRET:
            return Response({"error": "GHL_CLIENT_SECRET not configured"}, status=500)
        row = GhlToken.objects.filter(location_id__isnull=True).first()
        if not row:
            return Response(
                {"error": "No GHL company connection found. Please onboard first."},
                status=400,
            )
        try:
            token = services.post_token(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": row.refresh_token,
                    "user_type": "Company",
                }
            )
            services.persist_token(token)
            services.exchange_and_store_location(token)
        except services.GhlError as e:
            return Response({"error": str(e)}, status=502)
        return Response({"success": True})


class UpdateContactFromSaleView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        contact_id = request.data.get("contactId")
        line_items = request.data.get("lineItems") or []
        if not contact_id:
            return Response({"error": "Missing contactId"}, status=400)
        try:
            token, location_id = services.get_valid_location_token()
        except services.GhlError as e:
            return Response({"error": str(e)}, status=400)

        cf_res = requests.get(
            f"https://services.leadconnectorhq.com/locations/{location_id}/customFields",
            headers={
                "Authorization": f"Bearer {token}",
                "Version": "2021-07-28",
                "Accept": "application/json",
            },
            timeout=30,
        )
        if not cf_res.ok:
            return Response(
                {"error": f"GHL custom fields error {cf_res.status_code}: {cf_res.text}"},
                status=502,
            )
        fields = cf_res.json().get("customFields") or []

        def find_id(label: str):
            for f in fields:
                if (f.get("name") or "").lower().strip() == label.lower():
                    return f.get("id")
            return None

        health = next((li for li in line_items if li.get("kind") == "health"), None)
        life = next((li for li in line_items if li.get("kind") == "life"), None)
        addons = [li for li in line_items if li.get("kind") == "addon"]

        def policy_label(li):
            return f"{li.get('carrier')} - {li.get('product')}" if li else ""

        def premium_value(li):
            if not li:
                return ""
            amt = li.get("amount")
            return "" if amt in (None, "") else str(amt)

        # Always send every managed field so the contact stays in sync with the
        # sale: fields with no matching line item are cleared (empty string).
        desired = {
            "Health Insurance": policy_label(health),
            "Health Insurance Premium": premium_value(health),
            "Life Insurance": policy_label(life),
            "Life Insurance Premium": premium_value(life),
            "Addons": ", ".join(a.get("product") or "" for a in addons),
        }

        custom_fields = []
        for label, value in desired.items():
            fid = find_id(label)
            if fid:
                custom_fields.append({"id": fid, "value": value})

        if not custom_fields:
            return Response({"success": True, "updated": 0})

        up_res = requests.put(
            f"https://services.leadconnectorhq.com/contacts/{contact_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "Version": "2021-07-28",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={"customFields": custom_fields},
            timeout=30,
        )
        if not up_res.ok:
            return Response(
                {"error": f"GHL contact update error {up_res.status_code}: {up_res.text}"},
                status=502,
            )
        return Response({"success": True, "updated": len(custom_fields)})


# ---------------------------------------------------------------------------
# Public hooks (called by GHL, no auth)
# ---------------------------------------------------------------------------


def log_delivery(**entry):
    try:
        GhlWebhookLog.objects.create(
            status=entry.get("status"),
            type=entry.get("type"),
            entity_id=entry.get("entity_id"),
            entity_table=entry.get("entity_table"),
            action=entry.get("action"),
            error=entry.get("error"),
            payload=entry.get("payload"),
        )
    except Exception:
        logger.exception("ghl-webhook: failed to write audit log")


def build_name(p: dict) -> str | None:
    n = " ".join(filter(None, [p.get("firstName"), p.get("lastName")])).strip()
    return n or p.get("name") or None


def handle_event(payload: dict) -> dict:
    event_type = payload.get("type") or ""
    entity_id = payload.get("id")

    if not entity_id:
        log_delivery(status="skipped", type=event_type, error="missing id", payload=payload)
        return {"skipped": True, "reason": "missing id"}

    is_contact = event_type.startswith("Contact")
    is_user = event_type.startswith("User")
    if not is_contact and not is_user:
        log_delivery(
            status="skipped",
            type=event_type,
            entity_id=entity_id,
            error=f"unsupported type {event_type}",
            payload=payload,
        )
        return {"skipped": True, "reason": f"unsupported type {event_type}"}

    table = "ghl_contacts" if is_contact else "ghl_users"
    model = GhlContact if is_contact else GhlUser

    try:
        if event_type.endswith("Delete"):
            # Per requirement: do NOT delete the contact/user row. Just record the event.
            log_delivery(
                status="skipped",
                type=event_type,
                entity_id=entity_id,
                entity_table=table,
                action="delete-ignored",
                payload=payload,
            )
            return {"ok": True, "action": "delete-ignored", "table": table, "id": entity_id}

        name = build_name(payload)
        email = payload.get("email")
        phone = payload.get("phone")

        row = {
            "name": name,
            "email": email,
            "phone": phone,
            "type": event_type,
            "location_id": payload.get("locationId"),
            "raw": payload,
        }

        if is_contact:
            assigned = services.fetch_contact_assigned_user_id(
                entity_id, payload.get("locationId")
            )
            if assigned:
                row["user_id"] = assigned

        app_user_id = None
        if is_user:
            existing = GhlUser.objects.filter(id=entity_id).first()
            app_user_id = existing.app_user_id if existing else None

            created_with_default = False
            if not app_user_id and event_type != "UserDelete" and email:
                normalized = email.strip().lower()
                match = User.objects.filter(username__iexact=normalized).first()
                if match:
                    app_user_id = match.id
                else:
                    user = User.objects.create_user(
                        username=normalized, email=normalized, password=DEFAULT_GHL_PASSWORD
                    )
                    # Mirrors the handle_new_user trigger
                    Profile.objects.create(
                        user=user, display_name=name or normalized, email=normalized
                    )
                    UserRole.objects.create(user=user, role="agent")
                    app_user_id = user.id
                    created_with_default = True

            if app_user_id:
                row["app_user_id"] = app_user_id
                profile_update = {}
                if name:
                    profile_update["display_name"] = name
                if email:
                    profile_update["email"] = email
                if phone:
                    profile_update["phone"] = phone
                if created_with_default:
                    profile_update["must_change_password"] = True
                if profile_update:
                    Profile.objects.filter(pk=app_user_id).update(
                        **profile_update, updated_at=timezone.now()
                    )
                if email:
                    User.objects.filter(pk=app_user_id).update(
                        email=email.strip().lower(), username=email.strip().lower()
                    )

        obj = model.objects.filter(id=entity_id).first()
        if obj:
            for k, v in row.items():
                setattr(obj, k, v)
            obj.save()
        else:
            model.objects.create(id=entity_id, **row)

        log_delivery(
            status="success",
            type=event_type,
            entity_id=entity_id,
            entity_table=table,
            action="upserted+app-synced" if app_user_id else "upserted",
            payload=payload,
        )
        return {
            "ok": True,
            "action": "upserted",
            "table": table,
            "id": entity_id,
            "app_user_id": str(app_user_id) if app_user_id else None,
        }
    except Exception as err:
        log_delivery(
            status="error",
            type=event_type,
            entity_id=entity_id,
            entity_table=table,
            error=str(err),
            payload=payload,
        )
        raise


class GhlWebhookView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def options(self, request, *args, **kwargs):
        return Response(status=204)

    def post(self, request):
        body = request.data
        events = body if isinstance(body, list) else [body]
        results = []
        for evt in events:
            payload = evt.get("body") if isinstance(evt, dict) and "body" in evt else evt
            try:
                results.append(handle_event(payload))
            except Exception as e:
                logger.exception("ghl-webhook event error")
                results.append({"ok": False, "error": str(e)})
        return Response({"success": True, "results": results})


class GhlRefreshHookView(APIView):
    """Public cron hook that refreshes the company token and re-mints the
    location token, ported from /api/public/hooks/ghl-refresh."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        if not settings.GHL_CLIENT_SECRET:
            return Response({"error": "GHL_CLIENT_SECRET not configured"}, status=500)
        company_row = GhlToken.objects.filter(location_id__isnull=True).first()
        if not company_row:
            return Response({"skipped": "no company token"})

        try:
            token = services.post_token(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": company_row.refresh_token,
                    "user_type": "Company",
                }
            )
        except services.GhlError as e:
            return Response({"error": str(e)}, status=502)

        from datetime import timedelta

        expires_at = timezone.now() + timedelta(seconds=token.get("expires_in", 0))
        company_row.access_token = token["access_token"]
        company_row.refresh_token = token.get("refresh_token") or company_row.refresh_token
        company_row.expires_at = expires_at
        company_row.location_id = None
        company_row.company_id = token.get("companyId")
        company_row.user_type = token.get("userType") or "Company"
        company_row.scope = token.get("scope")
        company_row.raw = token
        company_row.save()

        location_result = None
        if token.get("companyId"):
            try:
                loc_tok = services.mint_location_token(
                    token["access_token"], token["companyId"], settings.GHL_LOCATION_ID
                )
            except services.GhlError as e:
                return Response(
                    {"company_refreshed": True, "location_error": str(e)}, status=502
                )
            loc_expires = timezone.now() + timedelta(seconds=loc_tok.get("expires_in", 0))
            loc_id = loc_tok.get("locationId") or settings.GHL_LOCATION_ID
            existing = GhlToken.objects.filter(location_id=loc_id).first()
            loc_row = {
                "access_token": loc_tok["access_token"],
                "refresh_token": loc_tok.get("refresh_token") or "",
                "expires_at": loc_expires,
                "location_id": loc_id,
                "company_id": loc_tok.get("companyId") or token.get("companyId"),
                "user_type": loc_tok.get("userType") or "Location",
                "scope": loc_tok.get("scope"),
                "raw": loc_tok,
            }
            if existing:
                for k, v in loc_row.items():
                    setattr(existing, k, v)
                existing.save()
            else:
                GhlToken.objects.create(**loc_row)
            location_result = {"location_id": loc_id, "expires_at": json_value(loc_expires)}

        return Response(
            {
                "success": True,
                "company": {
                    "expires_at": json_value(expires_at),
                    "company_id": token.get("companyId"),
                },
                "location": location_result,
            }
        )
