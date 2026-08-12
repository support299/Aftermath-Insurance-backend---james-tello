# Generated manually for sms_cost_per_lead

from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("company", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="companysettings",
            name="sms_cost_per_lead",
            field=models.DecimalField(
                decimal_places=6,
                default=Decimal("0.0102"),
                help_text="Dollar cost per lead/text used for CPA spend (leads × rate).",
                max_digits=10,
            ),
        ),
    ]
