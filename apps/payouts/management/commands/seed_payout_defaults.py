from django.core.management.base import BaseCommand

from apps.payouts.models import CompLevel, OnboardingMilestone, TrackerConfig


DEFAULT_LEVELS = [
    ("L1", "Level 1", 1),
    ("L2", "Level 2", 2),
    ("L3", "Level 3", 3),
    ("L4", "Level 4", 4),
    ("LEADER", "Leader", 10),
]

DEFAULT_MILESTONES = [
    {
        "slug": "sell-fnb",
        "name": "Sell FNB",
        "description": "$100 match when you sell an FNB policy.",
        "milestone_type": "product_sale",
        "threshold": 1,
        "match_value": "FNB",
        "cash_reward": 100,
        "sort_order": 1,
    },
    {
        "slug": "50k-sales",
        "name": "$50k in Sales",
        "description": "$100 match at $50,000 submitted AP.",
        "milestone_type": "submitted_ap",
        "threshold": 50000,
        "match_value": "",
        "cash_reward": 100,
        "sort_order": 2,
    },
    {
        "slug": "100k-sales",
        "name": "$100k in Sales",
        "description": "$250 match at $100,000 submitted AP.",
        "milestone_type": "submitted_ap",
        "threshold": 100000,
        "match_value": "",
        "cash_reward": 250,
        "sort_order": 3,
    },
    {
        "slug": "150k-sales",
        "name": "$150k in Sales",
        "description": "$300 match at $150,000 submitted AP.",
        "milestone_type": "submitted_ap",
        "threshold": 150000,
        "match_value": "",
        "cash_reward": 300,
        "sort_order": 4,
    },
    {
        "slug": "200k-sales",
        "name": "$200k in Sales",
        "description": "$500 match at $200,000 submitted AP.",
        "milestone_type": "submitted_ap",
        "threshold": 200000,
        "match_value": "",
        "cash_reward": 500,
        "sort_order": 5,
    },
]


class Command(BaseCommand):
    help = "Seed default comp levels, tracker config, and onboarding milestones."

    def handle(self, *args, **options):
        for code, name, sort_order in DEFAULT_LEVELS:
            CompLevel.objects.update_or_create(
                code=code,
                defaults={"name": name, "sort_order": sort_order, "is_active": True},
            )
            self.stdout.write(f"  level {code}")

        TrackerConfig.load()
        self.stdout.write("  tracker_config")

        for m in DEFAULT_MILESTONES:
            OnboardingMilestone.objects.update_or_create(
                slug=m["slug"],
                defaults={
                    "name": m["name"],
                    "description": m["description"],
                    "milestone_type": m["milestone_type"],
                    "threshold": m["threshold"],
                    "match_value": m.get("match_value", ""),
                    "cash_reward": m["cash_reward"],
                    "sort_order": m["sort_order"],
                    "is_active": True,
                },
            )
            self.stdout.write(f"  milestone {m['slug']}")

        self.stdout.write(self.style.SUCCESS("Payout defaults seeded."))
