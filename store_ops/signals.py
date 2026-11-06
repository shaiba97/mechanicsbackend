"""Signals for store_ops app.

Automatically creates a TransactionLedger when an order transitions to COMPLETED
with payment_status PAID, setting the payout due date to 7 days after completion
(7-day escrow hold window).
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from admin_api.models import PlatformCommissionRate
from customer.models import Order
from store_ops.models import TransactionLedger


@receiver(post_save, sender=Order)
def create_ledger_on_order_completed(sender, instance, created, **kwargs):
    """Create a TransactionLedger when an order is completed and paid.

    The payout due date is set to 7 days after the order was marked completed,
    giving a 7-day escrow hold window before the store payout becomes due.

    Ledger rows are immutable snapshots of the commission rate at completion:
    re-saving an old order never reflows its commission amounts.
    """
    if (
        instance.status != Order.Status.COMPLETED
        or instance.payment_status != Order.PaymentStatus.PAID
    ):
        return

    rate_obj = PlatformCommissionRate.objects.first()
    if rate_obj is None:
        rate_obj = PlatformCommissionRate.objects.create()
    rate = Decimal(str(rate_obj.rate))  # force Decimal even for in-memory defaults

    total = instance.grand_total_sar
    commission_amount = (total * rate).quantize(Decimal("0.01"))

    TransactionLedger.objects.get_or_create(
        order=instance,
        store=instance.store,
        defaults={
            "total_amount": total,
            "commission_percent": rate,
            "commission_amount": commission_amount,
            "store_payout_amount": total - commission_amount,
            "payout_status": TransactionLedger.PayoutStatus.HELD,
            "payout_due_at": instance.updated_at + timedelta(days=7),
        },
    )
