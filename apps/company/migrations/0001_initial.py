from django.db import migrations, models


def seed_singleton(apps, schema_editor):
    CompanySettings = apps.get_model("company", "CompanySettings")
    CompanySettings.objects.get_or_create(
        pk=1,
        defaults={"reporting_timezone": "America/New_York"},
    )


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="CompanySettings",
            fields=[
                (
                    "id",
                    models.PositiveSmallIntegerField(
                        default=1,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "reporting_timezone",
                    models.CharField(default="America/New_York", max_length=64),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "company_settings",
            },
        ),
        migrations.RunPython(seed_singleton, migrations.RunPython.noop),
    ]
