"""Delete all sales created by a historic import batch."""

import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.sales.models import Sale


class Command(BaseCommand):
    help = "Delete all sales tagged with the given import_batch_id (historic import rollback)."

    def add_arguments(self, parser):
        parser.add_argument("batch_id", help="UUID of the import batch to roll back.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many sales would be deleted without deleting.",
        )

    def handle(self, *args, **options):
        try:
            batch_id = uuid.UUID(options["batch_id"])
        except ValueError as exc:
            raise CommandError(f"Invalid batch UUID: {options['batch_id']}") from exc

        qs = Sale.objects.filter(import_batch_id=batch_id)
        count = qs.count()
        if count == 0:
            raise CommandError(f"No sales found for batch {batch_id}.")

        if options["dry_run"]:
            self.stdout.write(f"Would delete {count} sales (batch {batch_id}).")
            return

        with transaction.atomic():
            deleted, _ = qs.delete()

        self.stdout.write(
            self.style.SUCCESS(f"Deleted {deleted} sales (batch {batch_id}).")
        )
