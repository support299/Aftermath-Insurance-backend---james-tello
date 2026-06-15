from django.contrib import admin

from apps.teams.models import Team, TeamManager


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "manager", "created_at")
    search_fields = ("name",)


@admin.register(TeamManager)
class TeamManagerAdmin(admin.ModelAdmin):
    list_display = ("team", "user", "created_at")
