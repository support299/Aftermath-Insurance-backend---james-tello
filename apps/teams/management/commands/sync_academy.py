from django.core.management.base import BaseCommand

from apps.teams.academy_sync import push_snapshot


class Command(BaseCommand):
    help = "Push every dashboard team and agent into Academy onboarding."

    def handle(self, *args, **options):
        result = push_snapshot()
        self.stdout.write(self.style.SUCCESS(str(result)))
