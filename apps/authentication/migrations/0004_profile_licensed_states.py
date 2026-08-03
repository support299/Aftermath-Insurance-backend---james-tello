# Generated manually for licensed_states on profiles

import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("authentication", "0003_payouts_and_comp_level"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="licensed_states",
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.TextField(),
                blank=True,
                default=list,
                help_text="US state codes (e.g. TX, FL) where this agent is licensed.",
                size=None,
            ),
        ),
    ]
