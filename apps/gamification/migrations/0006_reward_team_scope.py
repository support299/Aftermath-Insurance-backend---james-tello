import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gamification", "0005_contest_team_scope"),
        ("teams", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="reward",
            name="team",
            field=models.ForeignKey(
                blank=True,
                db_column="team_id",
                help_text="Null = company-wide reward visible to all agents.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rewards",
                to="teams.team",
            ),
        ),
        migrations.AddIndex(
            model_name="reward",
            index=models.Index(fields=["team", "is_active"], name="rewards_team_id_act_idx"),
        ),
    ]
