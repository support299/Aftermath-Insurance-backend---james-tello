from django.core.management.base import BaseCommand

from apps.gamification.achievements import evaluate_all_achievements, seed_achievement_definitions


class Command(BaseCommand):
    help = "Evaluate and award period + lifetime achievements."

    def add_arguments(self, parser):
        parser.add_argument("--skip-seed", action="store_true")
        parser.add_argument("--monthly", action="store_true", help="Also evaluate monthly badges.")
        parser.add_argument(
            "--lifetime",
            action="store_true",
            help="Also scan all agents for lifetime badges (slow; normally handled per sale).",
        )

    def handle(self, *args, **options):
        if not options["skip_seed"]:
            seed_achievement_definitions()
        awarded = evaluate_all_achievements(
            include_monthly=options["monthly"],
            include_lifetime=options["lifetime"],
        )
        self.stdout.write(self.style.SUCCESS(f"Awarded {awarded} new achievement(s)."))
