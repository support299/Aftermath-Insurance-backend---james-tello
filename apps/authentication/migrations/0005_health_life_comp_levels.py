from django.db import migrations, models
import django.db.models.deletion


def backfill_profile_levels(apps, schema_editor):
    CompLevel = apps.get_model("payouts", "CompLevel")
    Profile = apps.get_model("authentication", "Profile")

    health_by_code = {lv.code: lv for lv in CompLevel.objects.filter(track="health")}
    life_by_code = {lv.code: lv for lv in CompLevel.objects.filter(track="life")}

    for p in Profile.objects.all():
        health_id = None
        life_id = None
        if p.comp_level_id:
            src = CompLevel.objects.filter(pk=p.comp_level_id).first()
            if src:
                code = src.code
                if src.track == "health":
                    health_id = src.id
                    life = life_by_code.get(code)
                    life_id = life.id if life else None
                else:
                    life_id = src.id
                    health = health_by_code.get(code)
                    health_id = health.id if health else None
        p.health_comp_level_id = health_id
        p.life_comp_level_id = life_id
        p.comp_level_id = health_id
        p.save(
            update_fields=[
                "health_comp_level_id",
                "life_comp_level_id",
                "comp_level_id",
            ]
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("authentication", "0004_profile_licensed_states"),
        ("payouts", "0002_health_life_comp_tracks"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="health_comp_level",
            field=models.ForeignKey(
                blank=True,
                db_column="health_comp_level_id",
                help_text="Drives health (+ add-on) product payout rates. Hidden from agents.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="health_profiles",
                to="payouts.complevel",
            ),
        ),
        migrations.AddField(
            model_name="profile",
            name="life_comp_level",
            field=models.ForeignKey(
                blank=True,
                db_column="life_comp_level_id",
                help_text="Drives life product payout rates. Hidden from agents.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="life_profiles",
                to="payouts.complevel",
            ),
        ),
        migrations.AlterField(
            model_name="profile",
            name="comp_level",
            field=models.ForeignKey(
                blank=True,
                db_column="comp_level_id",
                help_text="Deprecated: use health_comp_level / life_comp_level. Kept in sync with health.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="profiles",
                to="payouts.complevel",
            ),
        ),
        migrations.RunPython(backfill_profile_levels, noop_reverse),
    ]
