import uuid

from django.db import models


class StoreProfile(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        UNDER_REVIEW = "under_review", "Under Review"
        ACTION_REQUIRED = "action_required", "Action Required"

    store = models.OneToOneField(
        "authentication.AuthStore",
        on_delete=models.CASCADE,
        related_name="profile",
    )
    owner_full_name = models.CharField(max_length=200, blank=True, default="")
    national_id = models.CharField(max_length=64, blank=True, default="")
    mobile_phone = models.CharField(max_length=32, blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")
    commercial_name_ar = models.CharField(max_length=200, blank=True, default="")
    commercial_name_en = models.CharField(max_length=200, blank=True, default="")
    cr_number = models.CharField(max_length=10, blank=True, default="")
    vat_number = models.CharField(max_length=15, blank=True, default="")
    city = models.CharField(max_length=100, blank=True, default="")
    region = models.CharField(max_length=100, blank=True, default="")
    address_street = models.CharField(max_length=255, blank=True, default="")
    building_number = models.CharField(max_length=50, blank=True, default="")
    postal_code = models.CharField(max_length=20, blank=True, default="")
    lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTION_REQUIRED)
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "store_profiles"

    def __str__(self):
        return self.commercial_name_ar or self.store.name


class StoreSettings(models.Model):
    store = models.OneToOneField(
        "authentication.AuthStore",
        on_delete=models.CASCADE,
        related_name="settings",
    )
    bank_name = models.CharField(max_length=200, blank=True, default="")
    account_holder_name = models.CharField(max_length=200, blank=True, default="")
    iban = models.CharField(max_length=34, blank=True, default="")
    stripe_connected = models.BooleanField(default=False)
    currency = models.CharField(max_length=3, default="SAR")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "store_settings"

    def __str__(self):
        return self.store.name


class StoreBankAccount(models.Model):
    """Store bank account for Mada payouts."""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        "authentication.AuthStore",
        on_delete=models.CASCADE,
        related_name="bank_accounts",
    )
    bank_name = models.CharField(max_length=200, blank=True, default="")
    account_holder_name = models.CharField(max_length=200, blank=True, default="")
    iban = models.CharField(max_length=34, blank=True, default="")
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "store_bank_accounts"

    def __str__(self):
        return f"{self.store.name} - {self.iban}"


class KycDocument(models.Model):
    class DocType(models.TextChoices):
        CR = "cr", "Commercial Registration"
        VAT = "vat", "ZATCA VAT Certificate"
        IQAMA = "iqama", "Owner National ID / Iqama"

    class DocStatus(models.TextChoices):
        UPLOADED = "uploaded", "Uploaded"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"
        ACTION_NEEDED = "action_needed", "Action Needed"

    store = models.ForeignKey(
        "authentication.AuthStore",
        on_delete=models.CASCADE,
        related_name="kyc_documents",
    )
    doc_type = models.CharField(max_length=20, choices=DocType.choices)
    file_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    file_data = models.BinaryField()
    status = models.CharField(max_length=20, choices=DocStatus.choices, default=DocStatus.UPLOADED)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "store_kyc_documents"
        unique_together = [["store", "doc_type"]]

    def __str__(self):
        return f"{self.store} - {self.get_doc_type_display()}"
