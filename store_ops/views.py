import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from admin_api.models import PlatformCommissionRate
from customer.delivery import KsaDelivery
from customer.models import Order, Review
from inventory.models import InventoryItem
from store_ops.broadcast import (
    broadcast_customer_order_status,
    broadcast_order_status,
)
from store_ops.gateway import execute_iban_transfer
from store_ops.models import StoreDeliveryFee, TransactionLedger, StorePayoutRequest
from store_ops.validators import (
    resolve_ksa_bank,
    validate_account_holder_name,
    validate_sa_iban,
)
from store_profile.models import StoreBankAccount

# Transition rules: a store may move an order forward only along these paths.
ALLOWED_TRANSITIONS = {
    Order.Status.PENDING: {Order.Status.STORE_PREPARING, Order.Status.CANCELLED},
    Order.Status.STORE_PREPARING: {
        Order.Status.DELIVERY_ASSIGNED,
        Order.Status.OUT_FOR_DELIVERY,
        Order.Status.OTP_GENERATED,
    },
    Order.Status.DELIVERY_ASSIGNED: {
        Order.Status.OTP_GENERATED,
        Order.Status.OUT_FOR_DELIVERY,
    },
    Order.Status.OUT_FOR_DELIVERY: {Order.Status.OTP_GENERATED},
}

# OTP handover hardening (security): short-lived 4-digit codes with attempt
# throttling so an offline store terminal cannot brute-force the handover PIN.
OTP_CODE_DIGITS = 4
OTP_MAX_ATTEMPTS = 5
OTP_LOCKOUT_SECONDS = 15 * 60


def _otp_attempt_cache_key(order_id) -> str:
    return f"verify_otp_attempts:{order_id}"


def _require_store(request):
    store = getattr(request.user, "store", None)
    if store is None:
        raise PermissionDenied("Authenticated user is not linked to a store.")
    return store


def _holder_candidates(store) -> list[str]:
    """Names legally tied to the store's CR / National ID."""
    profile = getattr(store, "profile", None)
    candidates = [store.name]
    if profile:
        candidates.extend(
            [
                profile.commercial_name_en,
                profile.commercial_name_ar,
                profile.owner_full_name,
            ]
        )
    return [c for c in candidates if c]


def _field_error(field: str, exc: ValidationError) -> dict:
    """One structured error for the merchant UI (stable `code` keys)."""
    code = getattr(exc, "code", "") or "invalid"
    message = getattr(exc, "message", None)
    if message is None:
        message = str(exc)
    elif hasattr(message, "message"):
        message = str(message)
    return {"field": field, "code": code, "message": str(message)}


def _customer_name(order):
    user = order.user
    if user.full_name:
        return user.full_name
    email = user.email
    if email.isdigit() and email.startswith("966"):
        return f"+{email}"
    if email.isdigit():
        return f"+966{email}"
    return "Customer"


def _customer_phone(order):
    email = order.user.email
    if email.isdigit() and email.startswith("966"):
        return f"+{email}"
    if email.isdigit():
        return f"+966{email}"
    return ""


def _serialize_store_order(order):
    items = [
        {
            "id": str(item.pk),
            "partId": str(item.inventory_item_id),
            "partNumber": item.inventory_item.part_number,
            "partName": {
                "ar": item.inventory_item.part_name_ar,
                "en": item.inventory_item.part_name_en,
            },
            "quantity": item.quantity,
            "unitPriceSar": float(item.unit_price_sar),
            "lineTotalSar": float(item.line_total_sar),
        }
        for item in order.items.all()
    ]
    return {
        "id": str(order.pk),
        "status": order.status,
        "paymentStatus": order.payment_status,
        "paymentMethod": order.payment_method,
        "totalSar": float(order.total_sar),
        "deliveryFeeSar": float(order.delivery_fee_sar),
        "grandTotalSar": float(order.grand_total_sar),
        "city": order.city,
        "street": order.street,
        "customerName": _customer_name(order),
        "customerPhone": _customer_phone(order),
        "handoverOtp": (
            order.handover_otp if order.status == Order.Status.OTP_GENERATED else ""
        ),
        "createdAt": order.created_at.isoformat(),
        "updatedAt": order.updated_at.isoformat(),
        "items": items,
    }


def _paid_or_completed_q():
    return Q(status=Order.Status.COMPLETED) | Q(payment_status=Order.PaymentStatus.PAID)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def store_orders(request):
    store = _require_store(request)
    orders = (
        Order.objects.filter(store=store)
        .select_related("user")
        .prefetch_related("items__inventory_item")
    )
    order_status = request.query_params.get("status")
    active_statuses = [
        Order.Status.PENDING,
        Order.Status.STORE_PREPARING,
        Order.Status.DELIVERY_ASSIGNED,
        Order.Status.OTP_GENERATED,
    ]
    if order_status == "active":
        orders = orders.filter(status__in=active_statuses)
    elif order_status:
        orders = orders.filter(status=order_status)
    orders = orders.order_by("-created_at")[:150]
    data = [_serialize_store_order(order) for order in orders]
    counts = {}
    for key in (
        Order.Status.PENDING,
        Order.Status.STORE_PREPARING,
        Order.Status.DELIVERY_ASSIGNED,
        Order.Status.OTP_GENERATED,
        Order.Status.COMPLETED,
        Order.Status.CANCELLED,
    ):
        counts[key] = Order.objects.filter(store=store, status=key).count()
    return Response({"orders": data, "count": len(data), "statusCounts": counts})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def order_status(request, order_id):
    store = _require_store(request)
    target = str(request.data.get("status") or "").strip()
    if not target:
        return Response(
            {"detail": "Provide the target status."}, status=status.HTTP_400_BAD_REQUEST
        )
    try:
        order = (
            Order.objects.select_related("user")
            .get(id=order_id, store=store)
        )
    except (Order.DoesNotExist, ValueError):
        return Response(
            {"detail": "Order not found."}, status=status.HTTP_404_NOT_FOUND
        )

    allowed = ALLOWED_TRANSITIONS.get(order.status, set())
    if target not in allowed:
        return Response(
            {
                "detail": f"Cannot move order from '{order.status}' to '{target}'."
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    if target == Order.Status.CANCELLED and order.payment_status == Order.PaymentStatus.PAID:
        return Response(
            {"detail": "Paid orders cannot be cancelled here."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if target == Order.Status.OTP_GENERATED:
        order.handover_otp = f"{secrets.randbelow(10**OTP_CODE_DIGITS):0{OTP_CODE_DIGITS}d}"
    order.status = target
    order.save()
    payload = _serialize_store_order(
        Order.objects.select_related("user")
        .prefetch_related("items__inventory_item")
        .get(pk=order.pk)
    )
    broadcast_order_status(store.pk, order.pk, order.status)
    broadcast_customer_order_status(
        order.pk,
        order.status,
        order.handover_otp if order.status == Order.Status.OTP_GENERATED else None,
    )
    return Response(payload)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def verify_otp(request, order_id):
    store = _require_store(request)
    code = str(
        request.data.get("otp") or request.data.get("code") or ""
    ).strip()
    try:
        order = (
            Order.objects.select_related("user")
            .get(id=order_id, store=store)
        )
    except (Order.DoesNotExist, ValueError):
        return Response(
            {"detail": "Order not found."}, status=status.HTTP_404_NOT_FOUND
        )

    if order.status != Order.Status.OTP_GENERATED:
        return Response(
            {"detail": "The handover code is not active for this order."},
            status=status.HTTP_409_CONFLICT,
        )

    expected = order.handover_otp or ""
    if not (
        len(code) == OTP_CODE_DIGITS
        and code.isdigit()
        and secrets.compare_digest(code, expected)
    ):
        attempts = cache.get(_otp_attempt_cache_key(order_id), 0) + 1
        cache.set(_otp_attempt_cache_key(order_id), attempts, OTP_LOCKOUT_SECONDS)
        if attempts >= OTP_MAX_ATTEMPTS:
            return Response(
                {
                    "detail": "Too many wrong attempts. The handover code is locked for 15 minutes."
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        return Response(
            {"detail": "Wrong handover code."}, status=status.HTTP_400_BAD_REQUEST
        )

    cache.delete(_otp_attempt_cache_key(order_id))

    order.status = Order.Status.COMPLETED
    if (
        order.payment_method == Order.PaymentMethod.COD
        and order.payment_status != Order.PaymentStatus.PAID
    ):
        order.payment_status = Order.PaymentStatus.PAID
    order.save()
    payload = _serialize_store_order(
        Order.objects.select_related("user")
        .prefetch_related("items__inventory_item")
        .get(pk=order.pk)
    )
    broadcast_order_status(store.pk, order.pk, order.status)
    broadcast_customer_order_status(order.pk, order.status)
    return Response(payload)


def _review_payload(review):
    user = review.user
    customer = user.full_name or (
        f"+{user.email}" if user.email.isdigit() and user.email.startswith("966")
        else (f"+966{user.email}" if user.email.isdigit() else "Customer")
    )
    item = review.order_item.inventory_item
    return {
        "id": str(review.pk),
        "rating": review.rating,
        "comment": review.comment,
        "customerName": customer,
        "productId": str(item.pk),
        "productName": {"ar": item.part_name_ar, "en": item.part_name_en},
        "partNumber": item.part_number,
        "orderItemId": str(review.order_item_id),
        "createdAt": review.created_at.isoformat(),
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def store_reviews(request):
    store = _require_store(request)
    reviews = Review.objects.filter(
        order_item__inventory_item__store=store
    ).select_related("order_item__inventory_item", "user")
    product_id = request.query_params.get("product")
    if product_id:
        reviews = reviews.filter(order_item__inventory_item_id=product_id)
    reviews = reviews.order_by("-created_at")[:100]
    count = reviews.count()
    data = [_review_payload(review) for review in reviews]
    average = (
        round(sum(r["rating"] for r in data) / count, 1) if count else None
    )
    return Response({"results": data, "count": count, "average": average})


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def store_bank_account(request):
    """Get or create store bank account for payouts."""
    store = _require_store(request)

    if request.method == "GET":
        try:
            account = StoreBankAccount.objects.get(store=store)
            return Response({
                "id": str(account.id),
                "bankName": account.bank_name,
                "accountHolderName": account.account_holder_name,
                "iban": account.iban,
                "isVerified": account.is_verified,
            })
        except StoreBankAccount.DoesNotExist:
            return Response(
                {"detail": "Bank account not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

    data = request.data or {}

    errors: list[dict] = []
    canonical_bank: str | None = None
    holder: str | None = None
    iban: str | None = None

    try:
        canonical_bank = resolve_ksa_bank(str(data.get("bankName") or ""))
    except ValidationError as exc:
        errors.append(_field_error("bankName", exc))
    try:
        holder = validate_account_holder_name(
            str(data.get("accountHolderName") or ""), _holder_candidates(store)
        )
    except ValidationError as exc:
        errors.append(_field_error("accountHolderName", exc))
    try:
        iban = validate_sa_iban(str(data.get("iban") or ""))
    except ValidationError as exc:
        errors.append(_field_error("iban", exc))

    if errors:
        return Response(
            {"detail": errors[0]["message"], "errors": errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    account, created = StoreBankAccount.objects.update_or_create(
        store=store,
        defaults={
            "bank_name": canonical_bank,
            "account_holder_name": holder,
            "iban": iban,
        },
    )
    return Response({
        "id": str(account.id),
        "bankName": account.bank_name,
        "accountHolderName": account.account_holder_name,
        "iban": account.iban,
        "isVerified": account.is_verified,
    }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def store_payouts(request):
    """List the store's payout ledger and aggregate summaries.

    `status` is the *effective* status: HELD rows whose payout window has
    elapsed surface as `ready_for_payout` without a migration touching the DB.
    """
    store = _require_store(request)
    now = timezone.now()

    entry_filter = {
        "held": Q(payout_status=TransactionLedger.PayoutStatus.HELD, payout_due_at__gt=now),
        "ready_for_payout": Q(
            payout_status=TransactionLedger.PayoutStatus.HELD, payout_due_at__lte=now
        ),
        "processing": Q(payout_status=TransactionLedger.PayoutStatus.PROCESSING),
        "paid": Q(payout_status=TransactionLedger.PayoutStatus.PAID),
        "failed": Q(payout_status=TransactionLedger.PayoutStatus.FAILED),
    }

    ledger = (
        TransactionLedger.objects.filter(store=store)
        .aggregate(
            held_amount=Sum("store_payout_amount", filter=entry_filter["held"]),
            held_count=Count("id", filter=entry_filter["held"]),
            ready_amount=Sum("store_payout_amount", filter=entry_filter["ready_for_payout"]),
            ready_count=Count("id", filter=entry_filter["ready_for_payout"]),
            processing_amount=Sum("store_payout_amount", filter=entry_filter["processing"]),
            processing_count=Count("id", filter=entry_filter["processing"]),
            paid_amount=Sum("store_payout_amount", filter=entry_filter["paid"]),
            paid_count=Count("id", filter=entry_filter["paid"]),
            failed_amount=Sum("store_payout_amount", filter=entry_filter["failed"]),
            failed_count=Count("id", filter=entry_filter["failed"]),
        )
    )

    entries = TransactionLedger.objects.filter(store=store).order_by("-created_at")
    status_param = (request.query_params.get("status") or "").strip().lower()
    if status_param:
        if status_param not in entry_filter:
            return Response(
                {
                    "detail": "Invalid status filter.",
                    "validStatuses": list(entry_filter),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        entries = entries.filter(entry_filter[status_param])

    def _bucket(count, amount):
        return {
            "count": int(count or 0),
            "amountSar": float(amount or 0),
        }

    data = []
    for payout in entries:
        if payout.payout_status in (
            TransactionLedger.PayoutStatus.PROCESSING,
            TransactionLedger.PayoutStatus.PAID,
            TransactionLedger.PayoutStatus.FAILED,
        ):
            effective = payout.payout_status
        elif payout.payout_due_at <= now:
            effective = TransactionLedger.PayoutStatus.READY_FOR_PAYOUT
        else:
            effective = TransactionLedger.PayoutStatus.HELD
        data.append(
            {
                "id": str(payout.id),
                "orderId": str(payout.order.id),
                "orderReference": payout.order.reference_code or "",
                "storeId": str(payout.store.id),
                "storeName": payout.store.name,
                "totalAmount": float(payout.total_amount),
                "commissionPercent": float(payout.commission_percent) * 100,
                "commissionAmount": float(payout.commission_amount),
                "storePayoutAmount": float(payout.store_payout_amount),
                "status": effective,
                "payoutStatus": payout.payout_status,
                "payoutDueAt": payout.payout_due_at.isoformat() if payout.payout_due_at else None,
                "paidAt": payout.paid_at.isoformat() if payout.paid_at else None,
                "gatewayTransactionId": payout.gateway_transaction_id,
                "createdAt": payout.created_at.isoformat(),
            }
        )

    return Response(
        {
            "results": data,
            "summary": {
                "ledger": {
                    "held": _bucket(ledger["held_count"], ledger["held_amount"]),
                    "readyForPayout": _bucket(ledger["ready_count"], ledger["ready_amount"]),
                    "processing": _bucket(
                        ledger["processing_count"], ledger["processing_amount"]
                    ),
                    "paid": _bucket(ledger["paid_count"], ledger["paid_amount"]),
                    "failed": _bucket(ledger["failed_count"], ledger["failed_amount"]),
                }
            },
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def execute_payouts(request):
    """Execute payouts due: escrowed funds with a verified bank account

    each due HELD ledger row is transferred with the (simulated) gateway,
    statuses may transition HELD->PROCESSING->PAID/FAILED, and a
    StorePayoutRequest audit row is recorded per batch.
    """
    store = _require_store(request)
    now = timezone.now()

    account = StoreBankAccount.objects.filter(store=store, is_verified=True).first()
    if account is None:
        unverified = StoreBankAccount.objects.filter(store=store).exists()
        return Response(
            {
                "detail": "A verified bank account is required to execute payouts."
                if unverified
                else "Add a bank account before requesting payouts.",
                "code": "bank_account_unverified" if unverified else "bank_account_missing",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    held_payouts = TransactionLedger.objects.filter(
        store=store,
        payout_status=TransactionLedger.PayoutStatus.HELD,
        payout_due_at__lte=now,
    ).select_related("order", "store")

    if not held_payouts.exists():
        return Response(
            {"detail": "No payouts due for execution."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Import here to avoid circular imports (admin_api imports from store_ops)
    from admin_api.models import PlatformGatewayConfig

    from_iban = PlatformGatewayConfig.get_singleton().iban
    batch_amount = held_payouts.aggregate(batch=Sum("store_payout_amount"))["batch"]

    payout_request = StorePayoutRequest.objects.create(
        store=store, amount_sar=batch_amount, status=StorePayoutRequest.Status.PROCESSING
    )

    executed = []
    failed = []
    first_transfer_id = ""
    for payout in held_payouts:
        transfer_result = execute_iban_transfer(
            from_iban=from_iban,
            to_iban=account.iban,
            amount=payout.store_payout_amount,
            reference=f"PAYOUT-{payout.order.reference_code}",
        )
        if transfer_result["success"]:
            payout.payout_status = TransactionLedger.PayoutStatus.PAID
            payout.paid_at = now
            payout.gateway_transaction_id = transfer_result["transfer_id"]
            first_transfer_id = payout.gateway_transaction_id
            executed.append(
                {
                    "id": str(payout.id),
                    "storeId": str(payout.store.id),
                    "storeName": payout.store.name,
                    "amountSar": float(payout.store_payout_amount),
                    "gatewayTransactionId": payout.gateway_transaction_id,
                    "paidAt": payout.paid_at.isoformat(),
                }
            )
        else:
            payout.payout_status = TransactionLedger.PayoutStatus.FAILED
            payout.gateway_transaction_id = ""
            failed.append(
                {
                    "id": str(payout.id),
                    "amountSar": float(payout.store_payout_amount),
                    "error": transfer_result.get("error"),
                }
            )
        payout.save(
            update_fields=[
                "payout_status",
                "paid_at",
                "gateway_transaction_id",
            ]
        )

    if executed:
        payout_request.status = StorePayoutRequest.Status.PAID
        payout_request.gateway_transaction_id = first_transfer_id
    else:
        payout_request.status = StorePayoutRequest.Status.FAILED
    payout_request.save(update_fields=["status", "gateway_transaction_id", "updated_at"])

    return Response(
        {
            "executed": executed,
            "failed": failed,
            "count": len(executed),
            "failedCount": len(failed),
            "payoutRequestId": str(payout_request.id),
            "payoutRequestStatus": payout_request.status,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def delivery_fees(request):
    store = _require_store(request)

    def _payload():
        configured = {
            fee.city: fee
            for fee in StoreDeliveryFee.objects.filter(store=store)
        }
        cities = []
        for entry in KsaDelivery.list():
            row = configured.get(entry["slug"])
            cities.append(
                {
                    "slug": entry["slug"],
                    "name": entry["name"],
                    "defaultFee": entry["fee"],
                    "fee": float(row.fee) if row else None,
                }
            )
        return Response({"cities": cities})

    if request.method == "GET":
        return _payload()

    fees_payload = request.data.get("fees")
    if not isinstance(fees_payload, list):
        return Response(
            {"detail": "Provide fees as a list of {city, fee}."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    validated = []
    seen = set()
    for entry in fees_payload:
        if not isinstance(entry, dict):
            return Response(
                {"detail": "Each fee entry must be an object."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        city = str(entry.get("city") or "").strip().lower()
        if city not in KsaDelivery.SLUGS:
            return Response(
                {"detail": f"Unsupported city: {city}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if city in seen:
            return Response(
                {"detail": f"Duplicated city: {city}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        amount = entry.get("fee")
        if amount in (None, "", 0, "0"):
            continue  # absent / zero means "use the default fee"
        try:
            fee = Decimal(str(amount))
        except InvalidOperation:
            return Response(
                {"detail": f"Fee for {city} must be a number."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if fee <= 0 or fee > Decimal("999.00"):
            return Response(
                {"detail": f"Fee for {city} must be between 0.01 and 999.00 SAR."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        seen.add(city)
        validated.append({"city": city, "fee": fee})

    with transaction.atomic():
        StoreDeliveryFee.objects.filter(store=store).delete()
        StoreDeliveryFee.objects.bulk_create(
            [
                StoreDeliveryFee(store=store, city=row["city"], fee=row["fee"])
                for row in validated
            ]
        )
    return _payload()


def _revenue_queryset(store):
    return (
        Order.objects.filter(store=store)
        .exclude(status=Order.Status.CANCELLED)
        .filter(_paid_or_completed_q())
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def earnings(request):
    store = _require_store(request)
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=6)
    month_start = today_start - timedelta(days=29)

    base = _revenue_queryset(store)

    today_agg = (
        base.filter(created_at__gte=today_start)
        .aggregate(revenue=Sum("grand_total_sar"), count=Count("id"))
    )
    week_agg = (
        base.filter(created_at__gte=week_start)
        .aggregate(revenue=Sum("grand_total_sar"), count=Count("id"))
    )
    month_agg = (
        base.filter(created_at__gte=month_start)
        .aggregate(revenue=Sum("grand_total_sar"), count=Count("id"))
    )
    totals_agg = base.aggregate(
        revenue=Sum("grand_total_sar"), count=Count("id")
    )
    completed_count = Order.objects.filter(
        store=store, status=Order.Status.COMPLETED
    ).count()

    commission_rate = PlatformCommissionRate.get_rate()
    week_revenue = float(week_agg["revenue"] or 0)
    commission = round(week_revenue * commission_rate, 2)

    daily_rows = (
        base.filter(created_at__gte=week_start)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(revenue=Sum("grand_total_sar"), count=Count("id"))
        .order_by("day")
    )
    by_day = {row["day"]: row for row in daily_rows}
    daily = []
    for offset in range(6, -1, -1):
        day = (today_start - timedelta(days=offset)).date()
        row = by_day.get(day)
        daily.append(
            {
                "date": day.isoformat(),
                "revenueSar": float(row["revenue"] or 0) if row else 0.0,
                "ordersCount": row["count"] if row else 0,
            }
        )

    payout_agg = StorePayoutRequest.objects.filter(store=store).aggregate(
        requested=Sum("amount_sar"),
        paid=Sum("amount_sar", filter=Q(status="paid")),
    )
    pending_payouts = StorePayoutRequest.objects.filter(store=store).filter(
        Q(status="pending_review") | Q(status="processing")
    ).count()

    return Response(
        {
            "commissionRate": commission_rate,
            "today": {
                "revenueSar": float(today_agg["revenue"] or 0),
                "ordersCount": today_agg["count"] or 0,
            },
            "week": {
                "revenueSar": week_revenue,
                "ordersCount": week_agg["count"] or 0,
                "commissionSar": commission,
                "netSar": round(week_revenue - commission, 2),
            },
            "month": {
                "revenueSar": float(month_agg["revenue"] or 0),
                "ordersCount": month_agg["count"] or 0,
            },
            "totals": {
                "revenueSar": float(totals_agg["revenue"] or 0),
                "ordersCount": totals_agg["count"] or 0,
                "completedOrdersCount": completed_count,
            },
            "daily": daily,
            "payouts": {
                "requestedSar": float(payout_agg["requested"] or 0),
                "paidSar": float(payout_agg["paid"] or 0),
                "pendingCount": pending_payouts,
            },
        }
    )


def _period_range(period, now):
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1) - timedelta(microseconds=1)
    if period == "day":
        return today_start, today_end
    if period == "week":
        return today_start - timedelta(days=6), today_end
    if period == "month":
        return today_start.replace(day=1), today_end
    if period == "quarter":
        month = today_start.month
        q_start_month = ((month - 1) // 3) * 3 + 1
        return today_start.replace(month=q_start_month, day=1), today_end
    if period == "half":
        h_start_month = 1 if today_start.month <= 6 else 7
        return today_start.replace(month=h_start_month, day=1), today_end
    if period == "year":
        return today_start.replace(month=1, day=1), today_end
    return today_start - timedelta(days=6), today_end


def _aggregate_range(store, start, end):
    base = _revenue_queryset(store).filter(created_at__gte=start, created_at__lte=end)
    agg = base.aggregate(revenue=Sum("grand_total_sar"), count=Count("id"))
    gross_sales = float(agg["revenue"] or 0)
    order_count = int(agg["count"] or 0)
    completed_count = base.filter(status=Order.Status.COMPLETED).count()

    commission_rate = PlatformCommissionRate.get_rate()
    commission_sar = round(gross_sales * commission_rate, 2)
    vat_collected_sar = round(gross_sales * 0.15, 2)

    ledger_qs = TransactionLedger.objects.filter(
        store=store, created_at__gte=start, created_at__lte=end
    )
    paid_agg = ledger_qs.filter(
        payout_status=TransactionLedger.PayoutStatus.PAID
    ).aggregate(amount=Sum("store_payout_amount"))
    net_payout_sar = float(paid_agg["amount"] or 0) or round(
        gross_sales - commission_sar, 2
    )
    held_agg = ledger_qs.filter(
        payout_status=TransactionLedger.PayoutStatus.HELD
    ).aggregate(amount=Sum("store_payout_amount"))
    escrow_held_sar = float(held_agg["amount"] or 0)

    return {
        "grossSalesSar": round(gross_sales, 2),
        "commissionSar": commission_sar,
        "netPayoutSar": round(net_payout_sar, 2),
        "vatCollectedSar": vat_collected_sar,
        "escrowHeldSar": round(escrow_held_sar, 2),
        "orderCount": order_count,
        "completedCount": completed_count,
    }


def _month_rollups(store, start, end):
    rollups = []
    cursor = start.replace(day=1)
    end_date = end.date()
    while cursor.date() <= end_date:
        next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_end = min(next_month - timedelta(microseconds=1), end)
        agg = _aggregate_range(store, cursor, month_end)
        rollups.append(
            {
                "label": cursor.strftime("%B %Y"),
                "start": cursor.isoformat(),
                "end": month_end.isoformat(),
                **agg,
            }
        )
        cursor = next_month
    return rollups


def _accrual_breakdown(store, start, end):
    base = _revenue_queryset(store).filter(created_at__gte=start, created_at__lte=end)

    cash_agg = base.filter(payment_status=Order.PaymentStatus.PAID).aggregate(
        amount=Sum("grand_total_sar"), count=Count("id")
    )
    credit_agg = base.filter(
        status=Order.Status.COMPLETED, payment_status=Order.PaymentStatus.UNPAID
    ).aggregate(amount=Sum("grand_total_sar"), count=Count("id"))
    deferred_agg = (
        base.exclude(status=Order.Status.COMPLETED)
        .filter(payment_status=Order.PaymentStatus.PAID)
        .aggregate(amount=Sum("grand_total_sar"), count=Count("id"))
    )
    earned_agg = base.filter(status=Order.Status.COMPLETED).aggregate(
        amount=Sum("grand_total_sar")
    )
    return {
        "cashCollectedSar": round(float(cash_agg["amount"] or 0), 2),
        "creditSalesSar": round(float(credit_agg["amount"] or 0), 2),
        "deferredRevenueSar": round(float(deferred_agg["amount"] or 0), 2),
        "earnedRevenueSar": round(float(earned_agg["amount"] or 0), 2),
        "creditOrdersCount": int(credit_agg["count"] or 0),
        "deferredOrdersCount": int(deferred_agg["count"] or 0),
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def financials_summary(request):
    store = _require_store(request)
    period = request.query_params.get("period", "month")
    if period not in ("day", "week", "month", "quarter", "half", "year"):
        period = "month"
    now = timezone.now()
    start, end = _period_range(period, now)
    agg = _aggregate_range(store, start, end)
    accrual = _accrual_breakdown(store, start, end)
    rollup = None
    if period in ("month", "quarter", "half", "year"):
        rollup = _month_rollups(store, start, end)
    return Response(
        {
            "period": period,
            "rangeStart": start.isoformat(),
            "rangeEnd": end.isoformat(),
            "grossSalesSar": agg["grossSalesSar"],
            "commissionSar": agg["commissionSar"],
            "netPayoutSar": agg["netPayoutSar"],
            "vatCollectedSar": agg["vatCollectedSar"],
            "escrowHeldSar": agg["escrowHeldSar"],
            "orderCount": agg["orderCount"],
            "completedCount": agg["completedCount"],
            "accrual": accrual,
            "rollup": rollup,
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def flash_report(request):
    store = _require_store(request)
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1) - timedelta(microseconds=1)
    base = _revenue_queryset(store).filter(
        created_at__gte=today_start, created_at__lte=today_end
    )
    inflow_agg = base.aggregate(amount=Sum("grand_total_sar"))
    cash_mada_inflow_sar = float(inflow_agg["amount"] or 0)
    orders_fulfilled = base.filter(status=Order.Status.COMPLETED).count()
    pending_statuses = [
        Order.Status.PENDING,
        Order.Status.STORE_PREPARING,
        Order.Status.DELIVERY_ASSIGNED,
        Order.Status.OTP_GENERATED,
    ]
    pending_dispatches = base.filter(status__in=pending_statuses).count()
    inventory_alerts = InventoryItem.objects.filter(
        store=store,
        is_active=True,
        quantity__lte=F("min_stock_alert"),
    ).count()
    return Response(
        {
            "date": today_start.date().isoformat(),
            "cashMadaInflowSar": round(cash_mada_inflow_sar, 2),
            "ordersFulfilled": orders_fulfilled,
            "pendingDispatches": pending_dispatches,
            "inventoryAlerts": inventory_alerts,
        }
    )
