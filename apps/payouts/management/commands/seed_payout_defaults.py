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
        "slug": "first-sale",
        "name": "First Sale",
        "description": "$100 when you close your first deal.",
        "milestone_type": "first_sale",
        "threshold": 1,
        "cash_reward": 100,
        "sort_order": 1,
    },
    {
        "slug": "second-sale",
        "name": "Second Sale",
        "description": "$50 when you close your second deal.",
        "milestone_type": "sale_count",
        "threshold": 2,
        "cash_reward": 50,
        "sort_order": 2,
    },
    {
        "slug": "100k-submitted",
        "name": "$100K Submitted",
        "description": "Hit $100,000 in submitted annual premium during onboarding.",
        "milestone_type": "submitted_ap",
        "threshold": 100000,
        "cash_reward": 0,
        "sort_order": 3,
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
                    "cash_reward": m["cash_reward"],
                    "sort_order": m["sort_order"],
                    "is_active": True,
                },
            )
            self.stdout.write(f"  milestone {m['slug']}")

        self.stdout.write(self.style.SUCCESS("Payout defaults seeded."))
