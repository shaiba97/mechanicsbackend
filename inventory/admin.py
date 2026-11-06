from django.contrib import admin

from .models import InventoryItem


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = (
        'part_number',
        'store',
        'category',
        'condition',
        'quantity',
        'price',
        'is_active',
    )
    search_fields = ('part_number', 'part_name_ar', 'part_name_en')
    list_filter = ('condition', 'is_active')
    raw_id_fields = ('store', 'category', 'brand')
