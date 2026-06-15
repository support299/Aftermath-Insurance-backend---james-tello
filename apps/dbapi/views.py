"""PostgREST-style generic table API.

Speaks the same dialect the frontend Supabase shim emits:

  GET    /api/db/<table>/?select=...&col=eq.value&order=col.desc&limit=N
  POST   /api/db/<table>/          {"values": {...} | [...], "upsert": bool, "on_conflict": "col"}
  PATCH  /api/db/<table>/?col=eq.v {"values": {...}}
  DELETE /api/db/<table>/?col=eq.v

Responses: {"data": [...]} or {"error": {"message": ...}}.
Row shapes match what PostgREST returned in the original app.
"""

import datetime
import decimal
import uuid

from django.db import IntegrityError
from django.db.models import F, Q
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.dbapi.registry import DENY, TABLES

RESERVED_PARAMS = {"select", "order", "limit", "offset"}


def json_value(value):
    if isinstance(value, decimal.Decimal):
        f = float(value)
        return int(f) if f.is_integer() else f
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    return value


def parse_select(select: str):
    """Split a PostgREST select string into plain columns and embeds.

    "id, display_name, teams:team_id(name)" ->
      (["id", "display_name"], [("teams", "team_id", ["name"])])
    """
    parts, depth, current = [], 0, ""
    for ch in select:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current.strip())

    columns, embeds = [], []
    for part in parts:
        if "(" in part:
            head, inner = part.split("(", 1)
            inner = inner.rstrip(")")
            if ":" in head:
                alias, fk_col = head.split(":", 1)
            else:
                alias, fk_col = head, head
            subcols = [c.strip() for c in inner.split(",") if c.strip()]
            embeds.append((alias.strip(), fk_col.strip(), subcols))
        elif part:
            columns.append(part)
    return columns, embeds


def split_op(raw: str):
    """'eq.value' -> ('eq', 'value'); 'not.is.null' -> ('not.is', 'null')."""
    if raw.startswith("not."):
        rest = raw[4:]
        op, _, value = rest.partition(".")
        return f"not.{op}", value
    op, _, value = raw.partition(".")
    return op, value


def coerce_scalar(model, attname: str, value: str):
    """Coerce string filter values for fields Django won't parse from strings."""
    for f in model._meta.fields:
        if f.attname == attname:
            if f.get_internal_type() == "BooleanField":
                return value == "true"
            break
    return value


def build_filter(model, attname: str, op: str, value: str):
    """Translate a PostgREST operator into a Django Q (negate=True for not.*)."""
    negate = False
    if op.startswith("not."):
        negate = True
        op = op[4:]

    if op in ("eq", "neq", "gt", "gte", "lt", "lte"):
        value = coerce_scalar(model, attname, value)

    if op == "eq":
        q = Q(**{attname: value})
    elif op == "neq":
        q = ~Q(**{attname: value})
    elif op == "gt":
        q = Q(**{f"{attname}__gt": value})
    elif op == "gte":
        q = Q(**{f"{attname}__gte": value})
    elif op == "lt":
        q = Q(**{f"{attname}__lt": value})
    elif op == "lte":
        q = Q(**{f"{attname}__lte": value})
    elif op == "in":
        items = value.strip()
        if items.startswith("(") and items.endswith(")"):
            items = items[1:-1]
        values = [v.strip().strip('"') for v in items.split(",") if v.strip()]
        q = Q(**{f"{attname}__in": values})
    elif op == "is":
        if value == "null":
            q = Q(**{f"{attname}__isnull": True})
        else:
            q = Q(**{attname: value == "true"})
    elif op in ("like", "ilike"):
        pattern = value
        starts = pattern.startswith("%")
        ends = pattern.endswith("%")
        core = pattern.strip("%")
        prefix = "i" if op == "ilike" else ""
        if starts and ends:
            q = Q(**{f"{attname}__{prefix}contains": core})
        elif starts:
            q = Q(**{f"{attname}__{prefix}endswith": core})
        elif ends:
            q = Q(**{f"{attname}__{prefix}startswith": core})
        else:
            q = Q(**{f"{attname}__{'iexact' if op == 'ilike' else 'exact'}": core})
    elif op == "cs":
        q = Q(**{f"{attname}__contains": value})
    else:
        raise ValueError(f"Unsupported operator: {op}")

    return ~q if negate else q


def error_response(message: str, status: int = 400, code: str | None = None):
    body = {"error": {"message": message}}
    if code:
        body["error"]["code"] = code
    return Response(body, status=status)


class TableView(APIView):
    authentication_classes = APIView.authentication_classes
    permission_classes = [AllowAny]

    # ----- helpers -----------------------------------------------------------

    def get_config(self, table):
        return TABLES.get(table)

    def apply_filters(self, request, config, queryset):
        for key in request.query_params.keys():
            if key in RESERVED_PARAMS:
                continue
            attname = config.columns.get(key)
            if attname is None:
                raise ValueError(f"Unknown column: {key}")
            for raw in request.query_params.getlist(key):
                op, value = split_op(raw)
                queryset = queryset.filter(build_filter(config.model, attname, op, value))
        return queryset

    def apply_order_limit(self, request, config, queryset):
        order_params = request.query_params.getlist("order")
        order_by = []
        for raw in order_params:
            bits = raw.split(".")
            col = bits[0]
            attname = config.columns.get(col, col)
            ascending = "desc" not in bits[1:]
            expr = F(attname)
            nulls_first = "nullsfirst" in bits[1:]
            nulls_last = "nullslast" in bits[1:]
            if ascending:
                expr = expr.asc(nulls_first=nulls_first or None, nulls_last=nulls_last or None)
            else:
                expr = expr.desc(nulls_first=nulls_first or None, nulls_last=nulls_last or None)
            order_by.append(expr)
        if order_by:
            queryset = queryset.order_by(*order_by)
        limit = request.query_params.get("limit")
        if limit is not None:
            queryset = queryset[: int(limit)]
        return queryset

    def serialize_rows(self, config, queryset, select: str):
        columns, embeds = parse_select(select or "*")
        if columns == ["*"] or not columns:
            columns = list(config.columns.keys())

        select_related = []
        resolved_embeds = []
        for alias, fk_col, subcols in embeds:
            embed_info = config.embeds.get(fk_col)
            if not embed_info:
                continue
            target_table, accessor = embed_info
            target_config = TABLES[target_table]
            select_related.append(accessor)
            resolved_embeds.append((alias, accessor, subcols, target_config))

        if select_related:
            queryset = queryset.select_related(*select_related)

        rows = []
        for obj in queryset:
            row = {}
            for col in columns:
                attname = config.columns.get(col)
                if attname is None:
                    continue
                row[col] = json_value(getattr(obj, attname))
            for alias, accessor, subcols, target_config in resolved_embeds:
                related = getattr(obj, accessor)
                if related is None:
                    row[alias] = None
                else:
                    row[alias] = {
                        sub: json_value(getattr(related, target_config.columns.get(sub, sub)))
                        for sub in subcols
                    }
            rows.append(row)
        return rows

    def coerce_values(self, config, values: dict) -> dict:
        """Map exposed column names to model attnames; unknown columns rejected."""
        out = {}
        for key, val in values.items():
            attname = config.columns.get(key)
            if attname is None:
                raise ValueError(f"Unknown column: {key}")
            out[attname] = val
        return out

    # ----- HTTP methods -------------------------------------------------------

    def get(self, request, table):
        config = self.get_config(table)
        if config is None:
            return error_response(f"relation \"{table}\" does not exist", 404)
        scope = config.policy.select(request.user)
        if scope is DENY:
            # RLS semantics: unauthorized reads return empty sets, not errors
            return Response({"data": []})
        queryset = config.model.objects.all()
        if scope is not None:
            queryset = queryset.filter(scope)
        try:
            queryset = self.apply_filters(request, config, queryset)
            queryset = self.apply_order_limit(request, config, queryset)
            rows = self.serialize_rows(config, queryset, request.query_params.get("select", "*"))
        except ValueError as e:
            return error_response(str(e))
        return Response({"data": rows})

    def post(self, request, table):
        config = self.get_config(table)
        if config is None:
            return error_response(f"relation \"{table}\" does not exist", 404)
        body = request.data or {}
        values = body.get("values")
        if values is None:
            return error_response("Missing values")
        rows_in = values if isinstance(values, list) else [values]

        for row in rows_in:
            if not config.policy.insert(request.user, row):
                return error_response(
                    f"new row violates row-level security policy for table \"{table}\"",
                    403,
                    code="42501",
                )

        created = []
        try:
            for row in rows_in:
                data = self.coerce_values(config, row)
                if body.get("upsert"):
                    pk_field = config.model._meta.pk.attname
                    conflict_col = body.get("on_conflict") or "id"
                    conflict_attname = config.columns.get(conflict_col, conflict_col)
                    lookup_value = data.get(conflict_attname)
                    existing = config.model.objects.filter(
                        **{conflict_attname: lookup_value}
                    ).first()
                    if existing:
                        for k, v in data.items():
                            if k != pk_field:
                                setattr(existing, k, v)
                        existing.save()
                        created.append(existing)
                        continue
                obj = config.model(**data)
                obj.save(force_insert=True)
                created.append(obj)
        except (IntegrityError, ValueError, TypeError) as e:
            return error_response(str(e), 400)

        select = request.query_params.get("select")
        if select:
            ids = [obj.pk for obj in created]
            queryset = config.model.objects.filter(pk__in=ids)
            return Response({"data": self.serialize_rows(config, queryset, select)}, status=201)
        return Response({"data": []}, status=201)

    def patch(self, request, table):
        config = self.get_config(table)
        if config is None:
            return error_response(f"relation \"{table}\" does not exist", 404)
        scope = config.policy.update(request.user)
        if scope is DENY:
            return Response({"data": []})
        body = request.data or {}
        values = body.get("values") or {}
        queryset = config.model.objects.all()
        if scope is not None:
            queryset = queryset.filter(scope)
        try:
            queryset = self.apply_filters(request, config, queryset)
            data = self.coerce_values(config, values)
        except ValueError as e:
            return error_response(str(e))

        updated = []
        try:
            for obj in queryset:
                for k, v in data.items():
                    setattr(obj, k, v)
                obj.save()
                updated.append(obj)
        except (IntegrityError, ValueError, TypeError) as e:
            return error_response(str(e), 400)

        select = request.query_params.get("select")
        if select:
            ids = [obj.pk for obj in updated]
            qs = config.model.objects.filter(pk__in=ids)
            return Response({"data": self.serialize_rows(config, qs, select)})
        return Response({"data": []})

    def delete(self, request, table):
        config = self.get_config(table)
        if config is None:
            return error_response(f"relation \"{table}\" does not exist", 404)
        scope = config.policy.delete(request.user)
        if scope is DENY:
            return Response({"data": []})
        queryset = config.model.objects.all()
        if scope is not None:
            queryset = queryset.filter(scope)
        try:
            queryset = self.apply_filters(request, config, queryset)
        except ValueError as e:
            return error_response(str(e))
        try:
            queryset.delete()
        except IntegrityError as e:
            return error_response(str(e), 409)
        return Response({"data": []})
