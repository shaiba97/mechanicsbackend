from django.contrib import admin

from .models import PlatformCommissionRate


@admin.register(PlatformCommissionRate)
class PlatformCommissionRateAdmin(admin.ModelAdmin):
    list_display = ("rate", "updated_at", "updated_by")
    readonly_fields = ("updated_at",)
