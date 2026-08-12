# CPA benchmarks from client meeting: CPA under $160, ROI ~3x

from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("company", "0002_sms_cost_per_lead"),
    ]

    operations = [
        migrations.AddField(
            model_name="companysettings",
            name="cpa_cost_per_sale_target",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("160.00"),
                help_text="Cost per sale is considered good when at or under this amount.",
                max_digits=12,
            ),
        ),
        migrations.AddField(
            model_name="companysettings",
            name="cpa_roi_target_multiple",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("3.00"),
                help_text="ROI (deposits ÷ lead cost) is considered good at or above this multiple.",
                max_digits=6,
            ),
        ),
    ]
