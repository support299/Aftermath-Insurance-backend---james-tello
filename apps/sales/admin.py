from django.contrib import admin

from apps.sales.models import Sale


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ("sale_id", "agent_name", "team_name", "carrier", "product", "deal_size", "sale_date")
    search_fields = ("sale_id", "agent_name", "customer_name")
    list_filter = ("carrier",)
