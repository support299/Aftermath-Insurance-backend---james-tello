# Generated manually for historic sales import rollback support.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0003_sale_reporting_only"),
    ]

    operations = [
        migrations.AddField(
            model_name="sale",
            name="import_batch_id",
            field=models.UUIDField(
                blank=True,
                db_index=True,
                help_text="Set on bulk-imported sales so the batch can be rolled back.",
                null=True,
            ),
        ),
        migrations.AddIndex(
            model_name="sale",
            index=models.Index(fields=["import_batch_id"], name="idx_sales_import_batch"),
        ),
    ]
