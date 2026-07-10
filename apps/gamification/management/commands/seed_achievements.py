from django.core.management.base import BaseCommand

from apps.gamification.achievements import seed_achievement_definitions


class Command(BaseCommand):
    help = "Seed achievement badge definitions."

    def handle(self, *args, **options):
        seed_achievement_definitions()
        from apps.gamification.models import AchievementDefinition

        self.stdout.write(
            self.style.SUCCESS(
                f"Achievements ready ({AchievementDefinition.objects.count()} total)."
            )
        )
