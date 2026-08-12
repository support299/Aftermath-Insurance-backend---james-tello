# Manual lead cost entry per client demo (Aug 2026 meeting)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("expenses", "0002_cpa_entry"),
    ]

    operations = [
        migrations.AddField(
            model_name="cpaentry",
            name="lead_cost",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Dollar amount spent texting these leads (entered by the agent).",
                max_digits=12,
            ),
        ),
    ]
