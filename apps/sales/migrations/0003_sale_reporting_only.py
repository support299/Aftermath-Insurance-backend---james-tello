from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0002_sale_ghl_contact_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="sale",
            name="reporting_only",
            field=models.BooleanField(default=False),
        ),
    ]
