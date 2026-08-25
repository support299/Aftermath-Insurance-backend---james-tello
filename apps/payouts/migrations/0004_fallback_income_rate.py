from decimal import Decimal

from django.db import migrations, models


def bump_default_rate(apps, schema_editor):
    TrackerConfig = apps.get_model("payouts", "TrackerConfig")
    TrackerConfig.objects.filter(pk=1, blended_income_rate=Decimal("0.15")).update(
        blended_income_rate=Decimal("0.18")
    )


class Migration(migrations.Migration):
    dependencies = [
        ("payouts", "0003_alter_complevel_options"),
    ]

    operations = [
        migrations.AlterField(
            model_name="trackerconfig",
            name="blended_income_rate",
            field=models.DecimalField(
                decimal_places=4,
                default=Decimal("0.18"),
                help_text="Fallback income rate when a sale has no posted commission. $9k per $50k AP = 0.18.",
                max_digits=6,
            ),
        ),
        migrations.RunPython(bump_default_rate, migrations.RunPython.noop),
    ]
