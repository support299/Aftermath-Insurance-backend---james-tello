"""Idempotent sync of Supabase CSV exports into the Django database.

Re-runnable: every row is upserted by primary key (``INSERT ... ON CONFLICT``
semantics via ``bulk_create(update_conflicts=True)``), so existing records are
updated in place and nothing is ever duplicated.

Safety guarantees:
  * ``auth_users`` is NEVER touched here, so the bcrypt password hashes imported
    by ``import_auth_users`` are left completely alone. These CSVs only carry
    rows that *reference* users via foreign keys; they never write the User row.
  * ``created_at`` (auto_now_add) is preserved on existing rows — it is excluded
    from the update set, so re-syncing won't rewrite original creation times.

Orphaned user references:
  Some rows reference a Supabase user id that no longer maps 1:1 to ``auth_users``
  (e.g. an email that the password import attached to a pre-existing account under
  a different id). Such ids are remapped to the real account via email. Rows whose
  user still can't be resolved are skipped (reported as ``orphan``). Rows that would
  duplicate a non-PK unique key (e.g. a remapped (user, role) pair that already
  exists) are also skipped (reported as ``dup``).

Tables are processed in foreign-key dependency order so a fresh sync satisfies
all FK constraints. Each table runs in its own transaction; a failure in one
table is reported and does not roll back the others.

Usage:
    python manage.py sync_csv --dry-run          # parse + report, no writes
    python manage.py sync_csv                     # perform the sync
    python manage.py sync_csv --only sales,teams  # limit to specific tables
    python manage.py sync_csv --dir /path/to/csvs # CSV folder (default: BASE_DIR)
"""

import csv
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.management.base import BaseCommand, CommandError
from django.db import models, transaction
from django.utils.dateparse import parse_date, parse_datetime

from apps.authentication.models import Profile, User, UserRole
from apps.catalog.models import AddOn, Carrier, LeadSource, Product
from apps.expenses.models import Expense
from apps.ghl.models import GhlContact, GhlUser
from apps.sales.models import Sale
from apps.targets.models import Target
from apps.teams.models import Team, TeamManager

# (csv_prefix, model) in FK dependency order. auth_users is intentionally absent.
TABLE_SPECS = [
    ("carriers", Carrier),
    ("add_ons", AddOn),
    ("lead_sources", LeadSource),
    ("products", Product),          # -> carriers
    ("teams", Team),                # -> auth_users
    ("profiles", Profile),          # -> auth_users, teams
    ("team_managers", TeamManager), # -> teams, auth_users
    ("user_roles", UserRole),       # -> auth_users
    ("expenses", Expense),          # -> auth_users
    ("sales", Sale),                # -> auth_users, teams
    ("targets", Target),            # -> auth_users
    ("ghl_users", GhlUser),         # -> auth_users
    ("ghl_contacts", GhlContact),
]


class Command(BaseCommand):
    help = "Upsert Supabase CSV exports into the DB (idempotent; never touches auth_users/passwords)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            default=str(settings.BASE_DIR),
            help="Directory holding the *-export-*.csv files (default: BASE_DIR).",
        )
        parser.add_argument("--dry-run", action="store_true", help="Report only; no writes.")
        parser.add_argument(
            "--only",
            default="",
            help="Comma-separated csv prefixes to limit the sync (e.g. 'sales,teams').",
        )

    def handle(self, *args, **options):
        directory = Path(options["dir"])
        dry_run = options["dry_run"]
        only = {p.strip() for p in options["only"].split(",") if p.strip()}

        if not directory.is_dir():
            raise CommandError(f"Not a directory: {directory}")

        specs = [s for s in TABLE_SPECS if not only or s[0] in only]
        if only:
            unknown = only - {s[0] for s in TABLE_SPECS}
            if unknown:
                raise CommandError(f"Unknown table(s): {', '.join(sorted(unknown))}")

        self.db_user_ids = {str(i) for i in User.objects.values_list("id", flat=True)}
        self.user_remap = self._build_user_remap(directory)
        if self.user_remap:
            self.stdout.write(
                self.style.WARNING(
                    f"Remapping {len(self.user_remap)} orphaned user id(s) to existing "
                    "accounts via email."
                )
            )

        header = "DRY RUN — no writes" if dry_run else "SYNCING"
        self.stdout.write(self.style.WARNING(header))

        g_new = g_upd = g_bad = g_orphan = g_dup = 0
        for prefix, model in specs:
            path = self._latest_csv(directory, prefix)
            if path is None:
                self.stdout.write(f"  - {prefix}: no CSV found, skipping")
                continue
            try:
                new, upd, bad, orphan, dup = self._sync_table(model, path, dry_run)
            except Exception as exc:  # noqa: BLE001 - report and continue with other tables
                self.stderr.write(self.style.ERROR(f"  ! {prefix}: FAILED — {exc}"))
                continue
            g_new += new
            g_upd += upd
            g_bad += bad
            g_orphan += orphan
            g_dup += dup
            verb = "would insert/update" if dry_run else "inserted/updated"
            self.stdout.write(
                f"  - {prefix}: {verb} new={new} existing={upd} "
                f"orphan_skipped={orphan} dup_skipped={dup} bad_rows={bad} ({path.name})"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. new={g_new} existing={g_upd} orphan_skipped={g_orphan} "
                f"dup_skipped={g_dup} bad_rows={g_bad}"
            )
        )

    def _build_user_remap(self, directory: Path) -> dict[str, str]:
        """Map Supabase user ids missing from auth_users -> existing user id (by email)."""
        db_by_email = {
            (email or "").strip().lower(): str(uid)
            for uid, email in User.objects.values_list("id", "email")
            if email
        }

        # supabase_id -> email, gathered from the auth_users export and the profiles export.
        id_email: dict[str, str] = {}
        auth_csv = directory / "auth_users_export.csv"
        if auth_csv.exists():
            with auth_csv.open(newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):  # comma-delimited
                    sid = (r.get("id") or "").strip()
                    em = (r.get("email") or "").strip().lower()
                    if sid and em:
                        id_email.setdefault(sid, em)
        prof = self._latest_csv(directory, "profiles")
        if prof:
            with prof.open(newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh, delimiter=";"):
                    sid = (r.get("id") or "").strip()
                    em = (r.get("email") or "").strip().lower()
                    if sid and em:
                        id_email.setdefault(sid, em)

        remap = {}
        for sid, em in id_email.items():
            if sid not in self.db_user_ids and em in db_by_email:
                remap[sid] = db_by_email[em]
        return remap

    @staticmethod
    def _latest_csv(directory: Path, prefix: str) -> Path | None:
        # Timestamped filenames sort chronologically, so the last one is newest.
        matches = sorted(directory.glob(f"{prefix}-export-*.csv"))
        return matches[-1] if matches else None

    def _sync_table(self, model, path: Path, dry_run: bool):
        meta = model._meta
        col_to_field = {f.column: f for f in meta.concrete_fields}
        pk_field = meta.pk
        pk_attname = pk_field.attname

        # Foreign keys that point at the User model — subject to remap / orphan-skip.
        user_fk_attnames = {
            f.attname
            for f in meta.concrete_fields
            if f.is_relation and f.related_model is User
        }

        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh, delimiter=";")
            csv_columns = [c for c in (reader.fieldnames or []) if c in col_to_field]
            rows = list(reader)

        if not csv_columns:
            raise CommandError(f"No CSV columns map to model {model.__name__}")

        present = [col_to_field[c] for c in csv_columns]
        update_fields = [
            f.name for f in present if not f.primary_key and not getattr(f, "auto_now_add", False)
        ]

        existing_pks = {str(pk) for pk in model.objects.values_list("pk", flat=True)}
        unique_guards = self._build_unique_guards(model, set(csv_columns), pk_attname)

        objs, seen_pks, new, upd, bad, orphan, dup = [], set(), 0, 0, 0, 0, 0
        for row in rows:
            raw_pk = (row.get(pk_field.column) or "").strip()
            raw_pk = self.user_remap.get(raw_pk, raw_pk) if pk_attname in user_fk_attnames else raw_pk
            if not raw_pk or raw_pk in seen_pks:
                bad += not raw_pk
                continue

            try:
                kwargs = {
                    col_to_field[c].attname: self._coerce(col_to_field[c], row.get(c))
                    for c in csv_columns
                }
            except (ValueError, InvalidOperation, json.JSONDecodeError) as exc:
                bad += 1
                self.stderr.write(f"    skip {model.__name__} {raw_pk}: {exc}")
                continue

            # Remap user FKs; skip rows whose user still can't be resolved.
            resolved = True
            for attname in user_fk_attnames:
                val = kwargs.get(attname)
                if val in (None, ""):
                    continue
                val = self.user_remap.get(str(val), str(val))
                if str(val) not in self.db_user_ids:
                    resolved = False
                    break
                kwargs[attname] = val
            if not resolved:
                orphan += 1
                continue

            obj_pk = str(kwargs.get(pk_attname, raw_pk))
            if self._violates_unique(obj_pk, kwargs, unique_guards):
                dup += 1
                continue

            seen_pks.add(raw_pk)
            objs.append(model(**kwargs))
            if raw_pk in existing_pks:
                upd += 1
            else:
                new += 1

        if not dry_run and objs:
            with transaction.atomic():
                model.objects.bulk_create(
                    objs,
                    batch_size=500,
                    update_conflicts=True,
                    unique_fields=[pk_field.name],
                    update_fields=update_fields,
                )

        return new, upd, bad, orphan, dup

    def _build_unique_guards(self, model, csv_columns: set[str], pk_attname: str):
        """Return [(attnames, {value_tuple: pk})] for each non-PK unique key fully present in CSV."""
        meta = model._meta
        groups: list[list[str]] = []
        for f in meta.concrete_fields:
            if f.unique and not f.primary_key:
                groups.append([f.name])
        for ut in meta.unique_together:
            groups.append(list(ut))
        for c in meta.constraints:
            if isinstance(c, models.UniqueConstraint) and not c.condition:
                groups.append(list(c.fields))

        guards = []
        for names in groups:
            fields = [meta.get_field(n) for n in names]
            if not all(f.column in csv_columns for f in fields):
                continue  # can't reliably evaluate without all values present
            attnames = [f.attname for f in fields]
            existing: dict[tuple, str] = {}
            for vals in model.objects.values_list(pk_attname, *attnames):
                key = tuple(str(v) for v in vals[1:])
                existing[key] = str(vals[0])
            guards.append((attnames, existing))
        return guards

    @staticmethod
    def _violates_unique(obj_pk: str, kwargs: dict, guards) -> bool:
        for attnames, existing in guards:
            key = tuple(str(kwargs.get(a)) for a in attnames)
            owner = existing.get(key)
            if owner is not None and owner != obj_pk:
                return True  # the unique value already belongs to a different row
            existing[key] = obj_pk  # reserve within this batch too
        return False

    @staticmethod
    def _coerce(field, raw):
        if isinstance(raw, str):
            raw = raw.strip()

        if raw is None or raw == "":
            if field.null:
                return None
            if isinstance(field, ArrayField):
                return []
            if field.has_default():
                return field.get_default()
            return ""

        if isinstance(field, models.BooleanField):
            return raw.lower() in ("true", "t", "1", "yes")
        if isinstance(field, (models.JSONField, ArrayField)):
            return json.loads(raw)
        if isinstance(field, models.DateTimeField):
            return parse_datetime(raw)
        if isinstance(field, models.DateField):
            return parse_date(raw)
        if isinstance(field, models.DecimalField):
            return Decimal(raw)
        # UUID and text columns pass through as the raw string.
        return raw
