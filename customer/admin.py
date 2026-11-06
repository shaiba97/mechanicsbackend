from django.contrib import admin

from .models import CustomerVehicle, Order, OrderItem


@admin.register(CustomerVehicle)
class CustomerVehicleAdmin(admin.ModelAdmin):
    list_display = ("nickname", "vin", "make", "model", "year", "is_primary", "created_at")
    list_filter = ("is_primary", "make", "year")
    search_fields = ("nickname", "vin", "make", "model")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "store", "status", "payment_status", "total_sar", "created_at")
    list_filter = ("status", "payment_status", "created_at")
    search_fields = ("id", "user__email", "store__name")


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("order", "inventory_item", "quantity", "unit_price_sar", "line_total_sar")
    search_fields = ("order__id", "inventory_item__part_number")
