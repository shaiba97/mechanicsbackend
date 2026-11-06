import uuid

from django.db import models

from .fields import EncryptedCharField


class PlatformCommissionRate(models.Model):
    """Single platform-wide commission percentage applied to all stores/categories."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.1000)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "platform_commission_rate"

    def __str__(self):
        return f"{self.rate * 100}%"

    @classmethod
    def get_rate(cls):
        """Return the current commission rate as a float (e.g. 0.12 for 12%)."""
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create()
        return float(obj.rate)

    @classmethod
    def set_rate(cls, rate, updated_by=""):
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create(rate=rate, updated_by=updated_by)
        else:
            obj.rate = rate
            obj.updated_by = updated_by
            obj.save(update_fields=["rate", "updated_by", "updated_at"])
        return obj


class PlatformGatewayConfig(models.Model):
    """Singleton platform-wide gateway credentials for Mada/STC Pay/Visa/MasterCard/Apple Pay.

    API key secrets are encrypted at rest using django-cryptography.
    Only one row may exist; use get_singleton() to read/update it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    bank_name = models.CharField(max_length=200, blank=True, default="")
    account_holder_name = models.CharField(max_length=200, blank=True, default="")
    iban = models.CharField(max_length=34, blank=True, default="")
    swift_bic = models.CharField(max_length=11, blank=True, default="")

    mada_merchant_id = models.CharField(max_length=100, blank=True, default="")
    acquirer_api_key = EncryptedCharField(blank=True, default="")
    acquirer_api_secret = EncryptedCharField(blank=True, default="")

    supported_card_schemes = models.JSONField(
        default=list,
        blank=True,
        help_text="e.g. ['mada', 'stc_pay', 'visa', 'mastercard', 'apple_pay']",
    )

    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "platform_gateway_config"

    def __str__(self):
        return f"Platform Gateway Config ({self.bank_name or 'unconfigured'})"

    @classmethod
    def get_singleton(cls):
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create()
        return obj

    def save(self, *args, **kwargs):
        # Enforce singleton: only one row may exist.
        existing = self.__class__.objects.first()
        if existing and not self.pk:
            self.pk = existing.pk
        return super().save(*args, **kwargs)
