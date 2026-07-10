from django.core.management.base import BaseCommand

from apps.authentication.models import Profile
from apps.gamification.models import AgentProgress, LevelDefinition
from apps.gamification.services import (
    compute_streaks,
    level_for_xp,
    sale_xp,
    seed_level_definitions,
)
from apps.sales.models import Sale


class Command(BaseCommand):
    help = "Backfill agent XP, levels, and streaks from all historical sales."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-seed",
            action="store_true",
            help="Skip seeding level definitions.",
        )

    def handle(self, *args, **options):
        if not options["skip_seed"]:
            seed_level_definitions()
            self.stdout.write("Level definitions seeded.")

        levels = list(LevelDefinition.objects.order_by("rank"))
        agent_ids = set(Profile.objects.values_list("user_id", flat=True))

        xp_by_agent: dict = {}
        dates_by_agent: dict = {}

        self.stdout.write("Scanning sales…")
        for sale in Sale.objects.only("agent_id", "deal_size", "line_items", "sale_date").iterator(
            chunk_size=2000
        ):
            aid = sale.agent_id
            agent_ids.add(aid)
            xp_by_agent[aid] = xp_by_agent.get(aid, 0) + sale_xp(sale)
            if sale.sale_date:
                dates_by_agent.setdefault(aid, []).append(sale.sale_date)

        total = len(agent_ids)
        self.stdout.write(f"Updating progress for {total} agents…")

        batch = []
        for i, agent_id in enumerate(sorted(agent_ids, key=str), start=1):
            total_xp = xp_by_agent.get(agent_id, 0)
            sale_dates = dates_by_agent.get(agent_id, [])
            current_streak, best_streak = compute_streaks(sale_dates)
            last_sale_date = None
            if sale_dates:
                from apps.gamification.services import _reporting_tz, _to_local_date

                last_sale_date = _to_local_date(max(sale_dates), _reporting_tz())

            batch.append(
                AgentProgress(
                    agent_id=agent_id,
                    total_xp=total_xp,
                    level=level_for_xp(total_xp, levels),
                    current_streak=current_streak,
                    best_streak=best_streak,
                    last_sale_date=last_sale_date,
                )
            )
            if len(batch) >= 100:
                AgentProgress.objects.bulk_create(
                    batch,
                    update_conflicts=True,
                    unique_fields=["agent_id"],
                    update_fields=[
                        "total_xp",
                        "level",
                        "current_streak",
                        "best_streak",
                        "last_sale_date",
                    ],
                )
                batch.clear()
            if i % 50 == 0 or i == total:
                self.stdout.write(f"  {i}/{total} done")

        if batch:
            AgentProgress.objects.bulk_create(
                batch,
                update_conflicts=True,
                unique_fields=["agent_id"],
                update_fields=[
                    "total_xp",
                    "level",
                    "current_streak",
                    "best_streak",
                    "last_sale_date",
                ],
            )

        self.stdout.write(self.style.SUCCESS(f"Backfill complete for {total} agents."))
