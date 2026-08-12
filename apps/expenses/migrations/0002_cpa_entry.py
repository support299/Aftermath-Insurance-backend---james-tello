# Generated manually for CpaEntry

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("expenses", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CpaEntry",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "week_start",
                    models.DateField(
                        help_text="Monday of the week this entry covers (America/New_York week).",
                    ),
                ),
                ("leads_uploaded", models.PositiveIntegerField(default=0)),
                ("yes_count", models.PositiveIntegerField(default=0)),
                ("quoted_count", models.PositiveIntegerField(default=0)),
                ("sold_count", models.PositiveIntegerField(default=0)),
                ("check_amount", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "agent",
                    models.ForeignKey(
                        db_column="agent_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cpa_entries",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "cpa_entries",
            },
        ),
        migrations.AddIndex(
            model_name="cpaentry",
            index=models.Index(fields=["agent"], name="idx_cpa_entries_agent_id"),
        ),
        migrations.AddIndex(
            model_name="cpaentry",
            index=models.Index(fields=["week_start"], name="idx_cpa_entries_week_start"),
        ),
        migrations.AddConstraint(
            model_name="cpaentry",
            constraint=models.UniqueConstraint(
                fields=("agent", "week_start"),
                name="cpa_entries_agent_week_start_key",
            ),
        ),
    ]
