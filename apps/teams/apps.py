from django.apps import AppConfig


class TeamsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.teams"
    label = "teams"

    def ready(self):
        from apps.teams import signals  # noqa: F401
