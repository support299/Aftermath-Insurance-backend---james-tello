import uuid

from django.conf import settings
from django.db import models


class Team(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField(unique=True)
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="manager_id",
        related_name="managed_teams",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "teams"
        indexes = [models.Index(fields=["manager"])]

    def __str__(self) -> str:
        return self.name


class TeamManager(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    team = models.ForeignKey(
        Team, on_delete=models.CASCADE, db_column="team_id", related_name="team_managers"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        db_column="user_id",
        related_name="team_manager_rows",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "team_managers"
        constraints = [
            models.UniqueConstraint(
                fields=["team", "user"], name="team_managers_team_id_user_id_key"
            ),
        ]
        indexes = [models.Index(fields=["team"]), models.Index(fields=["user"])]
