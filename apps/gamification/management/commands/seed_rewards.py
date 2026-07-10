from django.core.management.base import BaseCommand

from apps.gamification.points import seed_rewards


class Command(BaseCommand):
    help = "Seed the rewards store catalog."

    def handle(self, *args, **options):
        seed_rewards()
        from apps.gamification.models import Reward

        self.stdout.write(self.style.SUCCESS(f"Rewards ready ({Reward.objects.count()} total)."))
