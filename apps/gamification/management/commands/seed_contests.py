from datetime import date, timedelta

from django.core.management.base import BaseCommand

from apps.gamification.models import Contest


class Command(BaseCommand):
    help = "Seed default active contests (e.g. cruise challenge)."

    def handle(self, *args, **options):
        today = date.today()
        end = today + timedelta(days=30)
        Contest.objects.update_or_create(
            title="Cruise Challenge",
            defaults={
                "description": "Top revenue producers win an exclusive team cruise!",
                "prize_description": "Caribbean cruise for two — top 3 agents by revenue",
                "metric": Contest.METRIC_REVENUE,
                "target_value": 50000,
                "start_date": today.replace(day=1),
                "end_date": end,
                "is_active": True,
            },
        )
        self.stdout.write(self.style.SUCCESS("Contests seeded."))
