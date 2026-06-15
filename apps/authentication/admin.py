from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from apps.authentication.models import LoginToken, Profile, User, UserRole


@admin.register(User)
class AppUserAdmin(UserAdmin):
    list_display = ("email", "username", "is_active", "last_login")
    ordering = ("email",)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "display_name", "email", "team", "must_change_password")
    search_fields = ("display_name", "email")


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "role")
    list_filter = ("role",)


@admin.register(LoginToken)
class LoginTokenAdmin(admin.ModelAdmin):
    list_display = ("token", "user", "expires_at", "used_at")
