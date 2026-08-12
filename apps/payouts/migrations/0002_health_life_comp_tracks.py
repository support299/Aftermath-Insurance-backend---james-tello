import uuid

from django.db import migrations, models


def clone_life_levels(apps, schema_editor):
    CompLevel = apps.get_model("payouts", "CompLevel")
    health_levels = list(CompLevel.objects.filter(track="health"))
    existing_life = {lv.code for lv in CompLevel.objects.filter(track="life")}
    for src in health_levels:
        if src.code in existing_life:
            continue
        CompLevel.objects.create(
            id=uuid.uuid4(),
            code=src.code,
            name=src.name,
            track="life",
            sort_order=src.sort_order,
            is_active=src.is_active,
        )


def noop_reverse(apps, schema_editor):
    CompLevel = apps.get_model("payouts", "CompLevel")
    CompLevel.objects.filter(track="life").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("payouts", "0001_payouts_and_comp_level"),
    ]

    operations = [
        migrations.AddField(
            model_name="complevel",
            name="track",
            field=models.CharField(
                choices=[("health", "Health"), ("life", "Life")],
                db_index=True,
                default="health",
                help_text="Health and Life use separate level ladders.",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="complevel",
            name="code",
            field=models.SlugField(max_length=32),
        ),
        migrations.AddConstraint(
            model_name="complevel",
            constraint=models.UniqueConstraint(
                fields=("track", "code"), name="comp_levels_track_code_uniq"
            ),
        ),
        migrations.RunPython(clone_life_levels, noop_reverse),
    ]
