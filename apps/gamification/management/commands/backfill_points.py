from django.core.management.base import BaseCommand

from apps.authentication.models import Profile
from apps.gamification.models import AgentProgress, PointTransaction
from apps.gamification.points import record_points, sale_points
from apps.sales.models import Sale


class Command(BaseCommand):
    help = "Backfill point balances from historical sales (one-time)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Clear existing sale/adjustment transactions and rebuild.",
        )

    def handle(self, *args, **options):
        if options["force"]:
            PointTransaction.objects.filter(
                reason__in=[PointTransaction.REASON_SALE, PointTransaction.REASON_ADJUSTMENT]
            ).delete()
            AgentProgress.objects.update(points_balance=0)

        if PointTransaction.objects.filter(reason=PointTransaction.REASON_SALE).exists():
            self.stdout.write("Sale point transactions already exist. Use --force to rebuild.")
            return

        agent_ids = set(Profile.objects.values_list("user_id", flat=True))
        totals: dict = {}

        self.stdout.write("Scanning sales for point backfill…")
        for sale in Sale.objects.only("id", "agent_id", "deal_size").iterator(chunk_size=2000):
            pts = sale_points(sale)
            totals[sale.agent_id] = totals.get(sale.agent_id, 0) + pts

        self.stdout.write(f"Writing balances for {len(totals)} agents…")
        for agent_id in agent_ids:
            total = totals.get(agent_id, 0)
            progress, _ = AgentProgress.objects.get_or_create(agent_id=agent_id)
            if total > 0:
                record_points(
                    agent_id,
                    total,
                    PointTransaction.REASON_ADJUSTMENT,
                    description=f"Historical sales backfill (+{total} pts)",
                )
            else:
                progress.points_balance = 0
                progress.save(update_fields=["points_balance", "updated_at"])

        self.stdout.write(self.style.SUCCESS("Point balances backfilled from sales."))
