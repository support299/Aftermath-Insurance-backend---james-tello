from decimal import Decimal

from django.db import migrations, models


OLD_SLUGS = ("first-sale", "second-sale", "100k-submitted")

NEW_INCENTIVES = [
    {
        "slug": "sell-fnb",
        "name": "Sell FNB",
        "description": "$100 match when you sell an FNB policy.",
        "milestone_type": "product_sale",
        "threshold": Decimal("1"),
        "match_value": "FNB",
        "cash_reward": Decimal("100"),
        "sort_order": 1,
    },
    {
        "slug": "50k-sales",
        "name": "$50k in Sales",
        "description": "$100 match at $50,000 submitted AP.",
        "milestone_type": "submitted_ap",
        "threshold": Decimal("50000"),
        "match_value": "",
        "cash_reward": Decimal("100"),
        "sort_order": 2,
    },
    {
        "slug": "100k-sales",
        "name": "$100k in Sales",
        "description": "$250 match at $100,000 submitted AP.",
        "milestone_type": "submitted_ap",
        "threshold": Decimal("100000"),
        "match_value": "",
        "cash_reward": Decimal("250"),
        "sort_order": 3,
    },
    {
        "slug": "150k-sales",
        "name": "$150k in Sales",
        "description": "$300 match at $150,000 submitted AP.",
        "milestone_type": "submitted_ap",
        "threshold": Decimal("150000"),
        "match_value": "",
        "cash_reward": Decimal("300"),
        "sort_order": 4,
    },
    {
        "slug": "200k-sales",
        "name": "$200k in Sales",
        "description": "$500 match at $200,000 submitted AP.",
        "milestone_type": "submitted_ap",
        "threshold": Decimal("200000"),
        "match_value": "",
        "cash_reward": Decimal("500"),
        "sort_order": 5,
    },
]


def replace_incentives(apps, schema_editor):
    OnboardingMilestone = apps.get_model("payouts", "OnboardingMilestone")
    OnboardingMilestone.objects.filter(slug__in=OLD_SLUGS).update(is_active=False)
    for row in NEW_INCENTIVES:
        OnboardingMilestone.objects.update_or_create(
            slug=row["slug"],
            defaults={
                "name": row["name"],
                "description": row["description"],
                "milestone_type": row["milestone_type"],
                "threshold": row["threshold"],
                "match_value": row["match_value"],
                "cash_reward": row["cash_reward"],
                "sort_order": row["sort_order"],
                "is_active": True,
            },
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("payouts", "0004_fallback_income_rate"),
    ]

    operations = [
        migrations.AddField(
            model_name="onboardingmilestone",
            name="match_value",
            field=models.CharField(
                blank=True,
                default="",
                help_text="For product_sale: match this text in product or carrier name (e.g. FNB).",
                max_length=64,
            ),
        ),
        migrations.AlterField(
            model_name="onboardingmilestone",
            name="milestone_type",
            field=models.CharField(
                choices=[
                    ("first_sale", "First sale"),
                    ("sale_count", "Nth sale"),
                    ("submitted_ap", "Submitted annual premium"),
                    ("product_sale", "Product / carrier match"),
                ],
                max_length=32,
            ),
        ),
        migrations.RunPython(replace_incentives, noop_reverse),
    ]
