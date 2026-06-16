"""Import users + bcrypt password hashes exported from Supabase ``auth.users``.

Supabase/GoTrue stores credentials as plain bcrypt hashes (``$2a$10$...``).
Django can verify those directly once ``BCryptPasswordHasher`` is enabled, but it
expects the algorithm tag as a prefix, so we store the hash verbatim as
``bcrypt$<supabase_hash>`` (never re-hashing it). On the user's next successful
login Django transparently upgrades the hash to the default PBKDF2 hasher.

Matching strategy (login is by email, so email is authoritative):
    1. match an existing user by ``id`` (the Supabase UUID), else
    2. match an existing user by email (handles rows that were re-created in the
       new system under a different UUID), else
    3. create a new user.

This avoids the ``auth_users_username_key`` unique violation that happens when a
CSV row's email already belongs to a different existing id.

Expected CSV columns (header row required):
    id, email, encrypted_password, email_confirmed_at, created_at, last_sign_in_at
"""

import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.dateparse import parse_datetime

from apps.authentication.models import User

BCRYPT_PREFIX = "bcrypt$"
DEFAULT_CSV = "auth_users_export.csv"


class Command(BaseCommand):
    help = "Import users and their bcrypt password hashes from a Supabase auth.users CSV export."

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_path",
            nargs="?",
            default=DEFAULT_CSV,
            help=f"Path to the CSV export (default: {DEFAULT_CSV} relative to BASE_DIR).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report without writing to the database.",
        )
        parser.add_argument(
            "--skip-existing-passwords",
            action="store_true",
            help=(
                "Do not overwrite the password of existing users that already have a "
                "usable one (protects passwords users changed after a previous import)."
            ),
        )

    def handle(self, *args, **options):
        path = Path(options["csv_path"])
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path
        if not path.exists():
            raise CommandError(f"CSV not found: {path}")

        dry_run = options["dry_run"]
        skip_existing = options["skip_existing_passwords"]

        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            raise CommandError("CSV has no data rows.")

        # Preload every existing user once (avoids per-row round-trips to a remote DB).
        existing = list(User.objects.all())
        by_id = {str(u.id): u for u in existing}
        by_email = {(u.email or u.username or "").strip().lower(): u for u in existing}

        # pk(str) -> {"obj": User, "is_new": bool}
        targets: dict[str, dict] = {}
        pw_skipped = bad = 0

        for row in rows:
            uid = (row.get("id") or "").strip()
            email = (row.get("email") or "").strip().lower()
            raw_hash = (row.get("encrypted_password") or "").strip()

            if not uid or not email:
                bad += 1
                self.stderr.write(f"  skip (missing id/email): {row!r}")
                continue
            if not raw_hash.startswith("$2"):
                bad += 1
                self.stderr.write(f"  skip {email}: unexpected hash format {raw_hash[:6]!r}")
                continue

            user = by_id.get(uid) or by_email.get(email)
            if user is not None:
                pk = str(user.id)
                if pk in targets:  # already resolved by an earlier (duplicate) row
                    continue
                entry = targets[pk] = {"obj": user, "is_new": False}
            else:
                if uid in targets:
                    continue
                user = User(id=uid, username=email, email=email, is_active=True)
                entry = targets[uid] = {"obj": user, "is_new": True}
                by_id[uid] = user
                by_email[email] = user

            obj = entry["obj"]
            if skip_existing and not entry["is_new"] and obj.has_usable_password():
                pw_skipped += 1
            else:
                obj.password = BCRYPT_PREFIX + raw_hash

            last_login = parse_datetime(row.get("last_sign_in_at") or "")
            if last_login:
                obj.last_login = last_login

        to_create = [t["obj"] for t in targets.values() if t["is_new"]]
        to_update = [t["obj"] for t in targets.values() if not t["is_new"]]

        if not dry_run:
            with transaction.atomic():
                if to_create:
                    User.objects.bulk_create(to_create, batch_size=200)
                if to_update:
                    User.objects.bulk_update(
                        to_update, ["password", "last_login"], batch_size=200
                    )

        summary = (
            f"{'DRY RUN — ' if dry_run else ''}"
            f"created={len(to_create)} updated={len(to_update)} "
            f"password_skipped={pw_skipped} bad_rows={bad} total={len(rows)}"
        )
        self.stdout.write(self.style.SUCCESS(summary))
