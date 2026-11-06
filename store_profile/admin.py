from django.contrib import admin

from .models import KycDocument, StoreProfile, StoreSettings


@admin.register(StoreProfile)
class StoreProfileAdmin(admin.ModelAdmin):
    list_display = (
        "commercial_name_ar",
        "commercial_name_en",
        "cr_number",
        "status",
        "is_verified",
        "updated_at",
    )
    list_filter = ("status", "is_verified")
    search_fields = ("commercial_name_ar", "commercial_name_en", "cr_number", "vat_number")
    raw_id_fields = ("store",)


@admin.register(StoreSettings)
class StoreSettingsAdmin(admin.ModelAdmin):
    list_display = ("store", "bank_name", "account_holder_name", "iban", "stripe_connected", "currency")
    list_filter = ("stripe_connected", "currency")
    search_fields = ("bank_name", "account_holder_name", "iban")
    raw_id_fields = ("store",)


@admin.register(KycDocument)
class KycDocumentAdmin(admin.ModelAdmin):
    list_display = ("store", "doc_type", "file_name", "content_type", "status", "uploaded_at")
    list_filter = ("doc_type", "status")
    search_fields = ("file_name",)
    raw_id_fields = ("store",)
