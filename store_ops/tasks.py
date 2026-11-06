"""Celery tasks for store payout processing."""

from celery import shared_task
from django.db.models import Sum
from django.utils import timezone

from store_ops.gateway import execute_iban_transfer
from store_ops.models import StorePayoutRequest, TransactionLedger
from store_profile.models import StoreBankAccount


@shared_task
def process_store_payouts():
    """Process all TransactionLedger entries whose escrow hold period has elapsed.

    Scans for ledger entries where:
      - payout_status == HELD
      - payout_due_at <= now

    Ledgers are grouped per store: a store without a verified bank account is
    skipped entirely. For processed batches, one StorePayoutRequest audit row
    is recorded per store with the gateway transfer reference.
    """
    now = timezone.now()

    due_ledgers = TransactionLedger.objects.filter(
        payout_status=TransactionLedger.PayoutStatus.HELD,
        payout_due_at__lte=now,
    )

    results = {"processed": 0, "success": 0, "failed": 0, "skipped": 0}

    # Import here to avoid circular imports (admin_api imports from store_ops)
    from admin_api.models import PlatformGatewayConfig

    platform = PlatformGatewayConfig.get_singleton()
    from_iban = platform.iban if platform else ""

    store_ids = due_ledgers.order_by("store_id").values_list("store_id", flat=True).distinct()
    for store_id in store_ids:
        store_ledgers = due_ledgers.filter(store_id=store_id).select_related(
            "store", "order"
        )
        store_account = StoreBankAccount.objects.filter(
            store_id=store_id, is_verified=True
        ).first()

        if not store_account:
            results["skipped"] += 1
            continue

        batch_amount = store_ledgers.aggregate(batch=Sum("store_payout_amount"))["batch"]
        payout_request = StorePayoutRequest.objects.create(
            store_id=store_id,
            amount_sar=batch_amount,
            status=StorePayoutRequest.Status.PROCESSING,
        )

        first_transfer_id = ""
        any_success = False
        for ledger in store_ledgers:
            transfer_result = execute_iban_transfer(
                from_iban=from_iban,
                to_iban=store_account.iban,
                amount=ledger.store_payout_amount,
                reference=f"PAYOUT-{ledger.order.reference_code}",
            )

            ledger.paid_at = now
            if transfer_result["success"]:
                ledger.payout_status = TransactionLedger.PayoutStatus.PAID
                ledger.gateway_transaction_id = transfer_result["transfer_id"]
                first_transfer_id = ledger.gateway_transaction_id
                any_success = True
                results["success"] += 1
            else:
                ledger.payout_status = TransactionLedger.PayoutStatus.FAILED
                ledger.gateway_transaction_id = ""
                results["failed"] += 1

            ledger.save(
                update_fields=["payout_status", "paid_at", "gateway_transaction_id"]
            )
            results["processed"] += 1

        if any_success:
            payout_request.status = StorePayoutRequest.Status.PAID
            payout_request.gateway_transaction_id = first_transfer_id
        else:
            payout_request.status = StorePayoutRequest.Status.FAILED
        payout_request.save(
            update_fields=["status", "gateway_transaction_id", "updated_at"]
        )

    return results
