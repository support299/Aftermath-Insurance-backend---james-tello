from django.core.management.base import BaseCommand

from apps.gamification.services import seed_level_definitions


class Command(BaseCommand):
    help = "Seed or update the default level ladder (Rookie → Hall of Fame)."

    def handle(self, *args, **options):
        seed_level_definitions()
        from apps.gamification.models import LevelDefinition

        count = LevelDefinition.objects.count()
        self.stdout.write(self.style.SUCCESS(f"Level definitions ready ({count} total)."))
