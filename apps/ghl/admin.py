from django.contrib import admin

from apps.ghl.models import GhlContact, GhlToken, GhlUser, GhlWebhookLog


@admin.register(GhlToken)
class GhlTokenAdmin(admin.ModelAdmin):
    list_display = ("location_id", "company_id", "user_type", "expires_at", "updated_at")


@admin.register(GhlUser)
class GhlUserAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "email", "app_user")
    search_fields = ("name", "email")


@admin.register(GhlContact)
class GhlContactAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "email", "user_id")
    search_fields = ("name", "email")


@admin.register(GhlWebhookLog)
class GhlWebhookLogAdmin(admin.ModelAdmin):
    list_display = ("status", "type", "entity_table", "entity_id", "created_at")
    list_filter = ("status",)
