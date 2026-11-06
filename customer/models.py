import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class CustomerVehicle(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="vehicles",
    )
    nickname = models.CharField(max_length=100, blank=True, default="")
    vin = models.CharField(max_length=17, blank=True, default="")
    make = models.CharField(max_length=100, blank=True, default="")
    model = models.CharField(max_length=100, blank=True, default="")
    year = models.PositiveIntegerField(null=True, blank=True)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customer_vehicles"
        ordering = ["-is_primary", "-created_at"]

    def __str__(self):
        return self.nickname or f"{self.year} {self.make} {self.model}"


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        STORE_PREPARING = "store_preparing", "Store Preparing"
        DELIVERY_ASSIGNED = "delivery_assigned", "Delivery Assigned"
        OUT_FOR_DELIVERY = "out_for_delivery", "Out for Delivery"
        OTP_GENERATED = "otp_generated", "OTP Generated"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class PaymentStatus(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    class PaymentMethod(models.TextChoices):
        COD = "cod", "Cash on Delivery"
        MOYASAR = "moyasar", "Moyasar"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="orders",
    )
    store = models.ForeignKey(
        "authentication.AuthStore",
        on_delete=models.CASCADE,
        related_name="customer_orders",
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.PENDING,
    )
    payment_status = models.CharField(
        max_length=30,
        choices=PaymentStatus.choices,
        default=PaymentStatus.UNPAID,
    )
    total_sar = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    delivery_fee_sar = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    grand_total_sar = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    city = models.CharField(max_length=50, blank=True, default="")
    street = models.CharField(max_length=200, blank=True, default="")
    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        default=PaymentMethod.MOYASAR,
    )
    checkout_session_id = models.CharField(max_length=255, blank=True, default="")
    handover_otp = models.CharField(max_length=6, blank=True, default="")
    reference_code = models.CharField(max_length=32, unique=True, blank=True, db_index=True)
    vehicle = models.ForeignKey(
        "customer.CustomerVehicle",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orders",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customer_orders"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order {str(self.id)[:8]} - {self.total_sar} SAR"

    def generate_reference_code(self):
        date_part = timezone.localdate(self.created_at or None).strftime("%Y%m%d")
        return f"ORD-{date_part}-{str(self.pk)[:8].upper()}"

    def save(self, *args, **kwargs):
        if not self.reference_code:
            self.reference_code = self.generate_reference_code()
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and not self._state.adding:
                kwargs["update_fields"] = [*update_fields, "reference_code"]
        super().save(*args, **kwargs)


class OrderItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )
    inventory_item = models.ForeignKey(
        "inventory.InventoryItem",
        on_delete=models.CASCADE,
        related_name="order_items",
    )
    quantity = models.PositiveIntegerField(default=1)
    unit_price_sar = models.DecimalField(max_digits=10, decimal_places=2)
    line_total_sar = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        db_table = "customer_order_items"

    def __str__(self):
        return f"{self.inventory_item.part_number} x{self.quantity}"

    def save(self, *args, **kwargs):
        self.line_total_sar = self.unit_price_sar * self.quantity
        super().save(*args, **kwargs)


class Review(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reviews",
    )
    order_item = models.ForeignKey(
        OrderItem,
        on_delete=models.CASCADE,
        related_name="reviews",
    )
    rating = models.PositiveSmallIntegerField(default=5)
    comment = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "customer_reviews"
        ordering = ["-created_at"]
        unique_together = ("user", "order_item")

    def __str__(self):
        return f"Review {self.rating}/5 by {self.user_id} on {self.order_item_id}"
