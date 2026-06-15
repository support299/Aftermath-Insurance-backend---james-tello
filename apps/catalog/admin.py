from django.contrib import admin

from apps.catalog.models import AddOn, Carrier, LeadSource, Product


@admin.register(Carrier)
class CarrierAdmin(admin.ModelAdmin):
    list_display = ("name", "carrier_type", "active")
    list_filter = ("carrier_type", "active")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "carrier", "active")
    list_filter = ("active",)


@admin.register(AddOn)
class AddOnAdmin(admin.ModelAdmin):
    list_display = ("name", "active")


@admin.register(LeadSource)
class LeadSourceAdmin(admin.ModelAdmin):
    list_display = ("name", "active")
