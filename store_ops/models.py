import uuid

from django.db import models


class StoreDeliveryFee(models.Model):
    """Per-store override of the global KSA delivery fee for one city."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        "authentication.AuthStore",
        on_delete=models.CASCADE,
        related_name="delivery_fees",
    )
    city = models.CharField(max_length=50)
    fee = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "store_delivery_fees"
        constraints = [
            models.UniqueConstraint(
                fields=["store", "city"],
                name="unique_store_city_delivery_fee",
            ),
        ]
        ordering = ["city"]

    def __str__(self):
        return f"{self.store_id}:{self.city}={self.fee}"


class TransactionLedger(models.Model):
    """Financial ledger entry for platform commission and store payout tracking."""

    class PayoutStatus(models.TextChoices):
        HELD = "held", "Held"
        READY_FOR_PAYOUT = "ready_for_payout", "Ready for Payout"
        PROCESSING = "processing", "Processing"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(
        "customer.Order",
        on_delete=models.CASCADE,
        related_name="transaction_ledgers",
    )
    store = models.ForeignKey(
        "authentication.AuthStore",
        on_delete=models.CASCADE,
        related_name="transaction_ledgers",
    )
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    commission_percent = models.DecimalField(max_digits=5, decimal_places=4)
    commission_amount = models.DecimalField(max_digits=10, decimal_places=2)
    store_payout_amount = models.DecimalField(max_digits=10, decimal_places=2)
    payout_status = models.CharField(
        max_length=20,
        choices=PayoutStatus.choices,
        default=PayoutStatus.HELD,
    )
    payout_due_at = models.DateTimeField()
    paid_at = models.DateTimeField(null=True, blank=True)
    gateway_transaction_id = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "transaction_ledger"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.store_id} - {self.store_payout_amount} SAR ({self.payout_status})"


class StorePayoutRequest(models.Model):
    """Store payout request tracking (platform-wide, gateway-agnostic)."""

    class Status(models.TextChoices):
        PENDING_REVIEW = "pending_review", "Pending Review"
        PROCESSING = "processing", "Processing"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        "authentication.AuthStore",
        on_delete=models.CASCADE,
        related_name="payout_requests",
    )
    amount_sar = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING_REVIEW,
    )
    gateway_transaction_id = models.CharField(max_length=255, null=True, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "store_payout_requests"
        ordering = ["-requested_at"]

    def __str__(self):
        return f"{self.amount_sar} SAR - {self.store.name} ({self.status})"


class AccrualAdjustment(models.Model):
    """Manual accrual-based accounting adjustment (deferred revenue, accrued expense, inventory write-off)."""

    class Type(models.TextChoices):
        DEFERRED_REVENUE = "deferred_revenue", "Deferred Revenue"
        ACCRUED_EXPENSE = "accrued_expense", "Accrued Expense"
        INVENTORY_WRITEOFF = "inventory_writeoff", "Inventory Write-off"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPLIED = "applied", "Applied"
        REVERSED = "reversed", "Reversed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        "authentication.AuthStore",
        on_delete=models.CASCADE,
        related_name="accrual_adjustments",
    )
    order = models.ForeignKey(
        "customer.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accrual_adjustments",
    )
    type = models.CharField(max_length=30, choices=Type.choices)
    amount_sar = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    notes = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accrual_adjustments"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.type} {self.amount_sar} SAR ({self.status})"
