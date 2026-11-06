from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse, Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from authentication.models import AuthStore, User
from customer.delivery import KsaDelivery
from customer.models import CustomerVehicle, Order
from inventory.models import InventoryItem
from store_ops.models import StoreDeliveryFee, TransactionLedger, AccrualAdjustment, StorePayoutRequest
from store_ops.tasks import process_store_payouts
from store_profile.models import KycDocument, StoreProfile, StoreSettings, StoreBankAccount
from .accounting import accounting_payload
from .models import PlatformCommissionRate, PlatformGatewayConfig
from .serializers import CommissionRateUpdateSerializer, PlatformCommissionRateSerializer

DOC_TYPE_LABELS = {
    KycDocument.DocType.CR: {"ar": "السجل التجاري", "en": "Commercial Registration"},
    KycDocument.DocType.VAT: {
        "ar": "شهادة ضريبة القيمة المضافة",
        "en": "ZATCA VAT Certificate",
    },
    KycDocument.DocType.IQAMA: {
        "ar": "بطاقة هوية / إقامة المالك",
        "en": "Owner National ID / Iqama",
    },
}


def _store_code(store):
    return f"STR-{str(store.id)[:5].upper()}"


def _bilingual_name(name):
    return {"ar": name or "", "en": name or ""}


def _derive_phone(email):
    email = email or ""
    if email.isdigit() and email.startswith("966"):
        return f"+{email}"
    if email.isdigit():
        return f"+966{email}"
    local = email.split("@", 1)[0] if "@" in email else ""
    if local.isdigit():
        if local.startswith("966"):
            return f"+{local}"
        return f"+966{local}"
    return ""


def _customer_display(user):
    name = user.full_name or ""
    if name:
        return name
    return _derive_phone(user.email) or "Customer"


def _store_status(store):
    profile = getattr(store, "profile", None)
    if profile is None:
        return "pending_approval"
    if profile.status == StoreProfile.Status.ACTIVE and profile.is_verified:
        return "verified"
    if profile.status == StoreProfile.Status.ACTION_REQUIRED:
        return "suspended"
    return "pending_approval"


def _serialize_store(store, orders_count=None):
    profile = getattr(store, "profile", None)
    return {
        "id": str(store.id),
        "name": store.name,
        "code": _store_code(store),
        "crNumber": store.cr_number or "",
        "status": _store_status(store),
        "isVerified": bool(profile and profile.is_verified),
        "city": (profile.city if profile else "") or "",
        "ordersCount": orders_count,
        "createdAt": store.created_at.isoformat() if store.created_at else "",
    }


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_stores(request):
    stores = (
        AuthStore.objects.select_related("profile")
        .annotate(orders_count=Count("customer_orders"))
        .order_by("-created_at")
    )
    q = request.query_params.get("q")
    if q:
        stores = stores.filter(Q(name__icontains=q) | Q(cr_number__icontains=q))
    return Response([_serialize_store(store, store.orders_count) for store in stores])


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_store_detail(request, store_id):
    try:
        store = AuthStore.objects.select_related("profile").get(id=store_id)
    except AuthStore.DoesNotExist:
        return Response({"detail": "Store not found."}, status=status.HTTP_404_NOT_FOUND)

    profile = getattr(store, "profile", None)
    members = User.objects.filter(store=store).select_related("store")
    documents = KycDocument.objects.filter(store=store).order_by("-uploaded_at")
    counts = Order.objects.filter(store=store).aggregate(
        ordersCount=Count("id"),
        completedOrders=Count("id", filter=Q(status=Order.Status.COMPLETED)),
        activeOrders=Count(
            "id",
            filter=Q(
                status__in=[
                    Order.Status.PENDING,
                    Order.Status.STORE_PREPARING,
                    Order.Status.DELIVERY_ASSIGNED,
                    Order.Status.OTP_GENERATED,
                ]
            ),
        ),
        salesSar=Sum("grand_total_sar", filter=Q(status=Order.Status.COMPLETED)),
    )
    counts["salesSar"] = round(float(counts["salesSar"] or 0), 2)

    data = {
        **_serialize_store(store, counts["ordersCount"]),
        "members": [
            {
                "id": str(m.id),
                "email": m.email,
                "fullName": m.full_name,
                "role": m.role,
            }
            for m in members
        ],
        "profile": None
        if profile is None
        else {
            "ownerFullName": profile.owner_full_name,
            "nationalId": profile.national_id,
            "mobilePhone": profile.mobile_phone,
            "contactEmail": profile.contact_email,
            "commercialNameAr": profile.commercial_name_ar,
            "commercialNameEn": profile.commercial_name_en,
            "crNumber": profile.cr_number or store.cr_number or "",
            "vatNumber": profile.vat_number,
            "city": profile.city,
            "region": profile.region,
            "addressStreet": profile.address_street,
            "buildingNumber": profile.building_number,
            "postalCode": profile.postal_code,
            "isVerified": profile.is_verified,
            "updatedAt": profile.updated_at.isoformat(),
        },
        "documents": [
            {
                "id": str(d.id),
                "docType": d.doc_type,
                "docTypeLabel": DOC_TYPE_LABELS.get(
                    d.doc_type, {"ar": d.doc_type, "en": d.doc_type}
                ),
                "fileName": d.file_name,
                "contentType": d.content_type,
                "status": d.status,
                "uploadedAt": d.uploaded_at.isoformat() if d.uploaded_at else "",
                "verifiedAt": d.verified_at.isoformat() if d.verified_at else None,
            }
            for d in documents
        ],
        "counts": {
            "ordersCount": counts["ordersCount"],
            "completedOrders": counts["completedOrders"],
            "activeOrders": counts["activeOrders"],
            "salesSar": counts["salesSar"],
        },
    }
    return Response(data)


STATUS_TO_PROFILE = {
    "verified": (StoreProfile.Status.ACTIVE, True),
    "suspended": (StoreProfile.Status.ACTION_REQUIRED, False),
    "pending_approval": (StoreProfile.Status.UNDER_REVIEW, False),
}


@api_view(["PATCH"])
@permission_classes([AllowAny])
def admin_store_status(request, store_id):
    try:
        store = AuthStore.objects.get(id=store_id)
    except AuthStore.DoesNotExist:
        return Response({"detail": "Store not found."}, status=status.HTTP_404_NOT_FOUND)

    status_value = request.data.get("status")
    if status_value not in STATUS_TO_PROFILE:
        return Response(
            {"detail": "Invalid status. Use verified, suspended, or pending_approval."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    profile_status, is_verified = STATUS_TO_PROFILE[status_value]
    profile, _created = StoreProfile.objects.get_or_create(store=store)
    profile.status = profile_status
    profile.is_verified = is_verified
    profile.save(update_fields=["status", "is_verified", "updated_at"])

    return Response(
        {
            "id": str(store.id),
            "name": store.name,
            "code": _store_code(store),
            "crNumber": store.cr_number or "",
            "status": _store_status(store),
            "updated": True,
        }
    )


def _store_bank_account_payload(store, account):
    return {
        "storeId": str(store.id),
        "storeName": store.name,
        "bank": {
            "id": str(account.id),
            "bankName": account.bank_name,
            "accountHolderName": account.account_holder_name,
            "iban": account.iban,
        },
        "isVerified": account.is_verified,
        "updatedAt": account.updated_at.isoformat() if account.updated_at else None,
    }


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_store_bank_account(request, store_id):
    try:
        store = AuthStore.objects.get(id=store_id)
    except AuthStore.DoesNotExist:
        return Response({"detail": "Store not found."}, status=status.HTTP_404_NOT_FOUND)
    try:
        account = StoreBankAccount.objects.get(store=store)
    except StoreBankAccount.DoesNotExist:
        return Response(
            {"detail": "Bank account not found."},
            status=status.HTTP_404_NOT_FOUND,
        )
    return Response(_store_bank_account_payload(store, account))


@api_view(["PUT", "PATCH"])
@permission_classes([AllowAny])
def admin_store_bank_account_verify(request, store_id):
    try:
        store = AuthStore.objects.get(id=store_id)
    except AuthStore.DoesNotExist:
        return Response({"detail": "Store not found."}, status=status.HTTP_404_NOT_FOUND)
    try:
        account = StoreBankAccount.objects.get(store=store)
    except StoreBankAccount.DoesNotExist:
        return Response(
            {"detail": "Bank account not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if "isVerified" not in request.data:
        return Response(
            {"detail": "isVerified (true|false) is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    verified = request.data.get("isVerified")
    if not isinstance(verified, bool):
        return Response(
            {"detail": "isVerified must be a boolean."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    account.is_verified = verified
    account.save(update_fields=["is_verified", "updated_at"])
    return Response(_store_bank_account_payload(store, account))


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_document_view(request, doc_id):
    try:
        doc = KycDocument.objects.get(id=doc_id)
    except KycDocument.DoesNotExist:
        raise Http404("Document not found.")

    response = HttpResponse(
        doc.file_data, content_type=doc.content_type or "application/octet-stream"
    )
    response["Content-Disposition"] = f'inline; filename="{doc.file_name}"'
    response["Content-Length"] = len(doc.file_data)
    return response


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_commission_rate(request):
    obj = PlatformCommissionRate.objects.first()
    if obj is None:
        obj = PlatformCommissionRate.objects.create()
    rate = float(obj.rate)
    return Response(
        {
            "id": str(obj.id),
            "rate": rate,
            "ratePercent": round(rate * 100, 4),
            "updatedAt": obj.updated_at.isoformat(),
            "updatedBy": obj.updated_by,
        }
    )


@api_view(["PATCH"])
@permission_classes([AllowAny])
def admin_commission_rate_update(request):
    serializer = CommissionRateUpdateSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    rate = serializer.validated_data["rate"]
    updated_by = getattr(request.user, "email", "") if request.user is not None and request.user.is_authenticated else ""
    obj = PlatformCommissionRate.set_rate(rate / 100, updated_by=updated_by)
    rate_value = float(obj.rate)
    return Response(
        {
            "id": str(obj.id),
            "rate": rate_value,
            "ratePercent": round(rate_value * 100, 4),
            "updatedAt": obj.updated_at.isoformat(),
            "updatedBy": obj.updated_by,
        }
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_inventory(request):
    items = InventoryItem.objects.select_related("category", "brand").all()
    q = request.query_params.get("q")
    if q:
        items = items.filter(
            Q(part_number__icontains=q)
            | Q(part_name_ar__icontains=q)
            | Q(part_name_en__icontains=q)
        )
    data = [
        {
            "id": str(item.id),
            "sku": item.part_number,
            "name": {
                "ar": item.part_name_ar,
                "en": item.part_name_en,
            },
            "brandId": str(item.brand_id) if item.brand_id else "",
            "brandName": item.brand.name if item.brand_id else None,
            "categoryId": str(item.category_id) if item.category_id else "",
            "categoryName": item.category.name if item.category_id else None,
            "priceSar": float(item.price),
            "stockQty": item.quantity,
            "isActive": item.is_active,
        }
        for item in items
    ]
    return Response(data)


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_dashboard_kpi(request):
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=6)

    daily_rows = (
        Order.objects.filter(created_at__gte=week_start)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(
            gmv=Sum(
                "grand_total_sar",
                filter=Q(status=Order.Status.COMPLETED),
            ),
            orders=Count("id"),
        )
    )
    by_day = {row["day"]: row for row in daily_rows}

    gmv_trend = []
    orders_trend = []
    for offset in range(6, -1, -1):
        day = timezone.localdate(today_start - timedelta(days=offset))
        row = by_day.get(day)
        gmv_trend.append(round(float(row["gmv"] or 0), 2) if row else 0.0)
        orders_trend.append(int(row["orders"] or 0) if row else 0)

    order_totals = Order.objects.aggregate(
        total=Count("id"),
        completed=Count("id", filter=Q(status=Order.Status.COMPLETED)),
        gmv=Sum("grand_total_sar", filter=Q(status=Order.Status.COMPLETED)),
    )
    gmv = round(float(order_totals["gmv"] or 0), 2)

    commission_rate = PlatformCommissionRate.get_rate()
    commission_sar = round(gmv * commission_rate, 2)

    store_totals = StoreProfile.objects.aggregate(
        active=Count("id", filter=Q(status=StoreProfile.Status.ACTIVE)),
        verified=Count(
            "id",
            filter=Q(status=StoreProfile.Status.ACTIVE, is_verified=True),
        ),
        pending=Count("id", filter=Q(status=StoreProfile.Status.UNDER_REVIEW)),
        suspended=Count("id", filter=Q(status=StoreProfile.Status.ACTION_REQUIRED)),
    )

    live_orders_by_region = (
        Order.objects.values("city")
        .annotate(count=Count("id"))
        .exclude(city="")
        .exclude(status=Order.Status.COMPLETED)
        .exclude(status=Order.Status.CANCELLED)
    )
    region_map = {}
    for entry in live_orders_by_region:
        city = entry["city"] or "unknown"
        region_map[city] = entry["count"]

    return Response(
        {
            "gmvSar": gmv,
            "gmvTrendWeek": gmv_trend,
            "ordersTrendWeek": orders_trend,
            "commissionSar": commission_sar,
            "takeRate": commission_rate,
            "storesActive": int(store_totals["active"] or 0),
            "storesVerified": int(store_totals["verified"] or 0),
            "storesPending": int(store_totals["pending"] or 0),
            "storesSuspended": int(store_totals["suspended"] or 0),
            "liveOrdersByRegion": region_map,
            "regionLabels": {row["slug"]: row["name"] for row in KsaDelivery.list()},
            "totalOrders": int(order_totals["total"] or 0),
            "completedOrders": int(order_totals["completed"] or 0),
        }
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_orders(request):
    orders = (
        Order.objects.select_related(
            "user", "store", "store__profile", "vehicle"
        )
        .prefetch_related("items__inventory_item", "user__vehicles")
        .order_by("-created_at")
    )
    q = request.query_params.get("q")
    if q:
        orders = orders.filter(
            Q(reference_code__icontains=q)
            | Q(city__icontains=q)
            | Q(street__icontains=q)
            | Q(user__full_name__icontains=q)
            | Q(user__email__icontains=q)
            | Q(store__name__icontains=q)
        )
    try:
        limit = min(max(int(request.query_params.get("limit", 200)), 1), 500)
    except (TypeError, ValueError):
        limit = 200

    data = []
    for order in orders[:limit]:
        user = order.user
        vin = ""
        if order.vehicle and order.vehicle.vin:
            vin = order.vehicle.vin
        else:
            for vehicle in user.vehicles.all():
                if vehicle.vin:
                    vin = vehicle.vin
                    break
        parts = [
            {
                "ar": item.inventory_item.part_name_ar,
                "en": item.inventory_item.part_name_en,
            }
            for item in order.items.all()
        ]
        data.append(
            {
                "id": str(order.id),
                "referenceCode": order.reference_code or "",
                "customerName": _bilingual_name(_customer_display(user)),
                "customerPhone": _derive_phone(user.email),
                "vin": vin,
                "parts": parts,
                "region": order.city or "",
                "storeId": str(order.store_id),
                "storeName": _bilingual_name(order.store.name),
                "storeCode": _store_code(order.store),
                "status": order.status,
                "paymentStatus": order.payment_status,
                "paymentMethod": order.payment_method,
                "totalSar": float(order.grand_total_sar),
                "receivedAt": order.created_at.isoformat(),
            }
        )
    return Response(data)


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_commissions(request):
    commission_rate = PlatformCommissionRate.get_rate()
    stores = AuthStore.objects.select_related("profile").all()

    store_totals = {
        row["store_id"]: row
        for row in Order.objects.filter(status=Order.Status.COMPLETED)
        .values("store_id")
        .annotate(gross=Sum("grand_total_sar"), orders=Count("id"))
    }

    latest_payouts = {}
    for payout in StorePayoutRequest.objects.order_by("-requested_at"):
        latest_payouts.setdefault(payout.store_id, payout)

    data = []
    for store in stores:
        row = store_totals.get(store.id)
        gross = float(row["gross"] or 0) if row else 0.0
        platform_fee = gross * commission_rate
        payout = gross - platform_fee
        payout_request = latest_payouts.get(store.id)
        profile = getattr(store, "profile", None)
        data.append(
            {
                "id": str(store.id),
                "storeId": str(store.id),
                "storeCode": _store_code(store),
                "storeName": store.name,
                "city": (profile.city if profile else "") or "",
                "iban": "",
                "grossSar": round(gross, 2),
                "platformFeeSar": round(platform_fee, 2),
                "payoutSar": round(payout, 2),
                "status": (payout_request.status if payout_request else "pending"),
                "settledAt": (
                    payout_request.updated_at.isoformat() if payout_request else ""
                ),
            }
        )
    return Response(data)


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_zatca_invoices(request):
    return Response([])


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_customers(request):
    customers = (
        User.objects.filter(role=User.Role.CUSTOMER)
        .annotate(
            orders_count=Count("orders", distinct=True),
            last_order_at=Max("orders__created_at"),
            lifetime_gmv=Sum(
                "orders__grand_total_sar",
                filter=Q(orders__status=Order.Status.COMPLETED),
            ),
        )
        .order_by("-created_at")
    )
    customers = customers.prefetch_related("vehicles")

    data = [
        {
            "id": str(customer.id),
            "name": _bilingual_name(_customer_display(customer)),
            "phone": _derive_phone(customer.email),
            "region": customer.city or "",
            "vehicles": [
                {
                    "id": str(vehicle.id),
                    "nickname": vehicle.nickname,
                    "vin": vehicle.vin,
                    "make": vehicle.make,
                    "model": vehicle.model,
                    "year": vehicle.year,
                    "isPrimary": vehicle.is_primary,
                }
                for vehicle in customer.vehicles.all()
            ],
            "ordersCount": customer.orders_count,
            "lifetimeGmvSar": round(float(customer.lifetime_gmv or 0), 2),
            "lastOrderAt": (
                customer.last_order_at.isoformat() if customer.last_order_at else ""
            ),
            "createdAt": customer.created_at.isoformat() if customer.created_at else "",
            "isActive": customer.is_active,
            "status": "active" if customer.is_active else "suspended",
        }
        for customer in customers
    ]
    return Response(data)


@api_view(["PATCH"])
@permission_classes([AllowAny])
def admin_customer_status(request, user_id):
    try:
        user = User.objects.get(id=user_id, role=User.Role.CUSTOMER)
    except (User.DoesNotExist, ValueError, TypeError):
        return Response({"detail": "Customer not found."}, status=status.HTTP_404_NOT_FOUND)

    new_status = request.data.get("status")
    if new_status not in ("active", "suspended"):
        return Response(
            {"detail": "Invalid status. Use active or suspended."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    user.is_active = new_status == "active"
    user.save(update_fields=["is_active"])
    return Response(
        {
            "id": str(user.id),
            "email": user.email,
            "fullName": user.full_name,
            "isActive": user.is_active,
            "status": "active" if user.is_active else "suspended",
        }
    )


@api_view(["DELETE"])
@permission_classes([AllowAny])
def admin_customer_delete(request, user_id):
    try:
        user = User.objects.get(id=user_id, role=User.Role.CUSTOMER)
    except (User.DoesNotExist, ValueError, TypeError):
        return Response({"detail": "Customer not found."}, status=status.HTTP_404_NOT_FOUND)

    user.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_notifications(request):
    return Response([])


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_delivery_overview(request):
    try:
        days = min(max(int(request.query_params.get("days", 7)), 1), 30)
    except (TypeError, ValueError):
        days = 7
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = today_start - timedelta(days=days - 1)
    today_end = today_start + timedelta(days=1) - timedelta(microseconds=1)

    window = Order.objects.filter(
        created_at__gte=start, created_at__lte=today_end
    )
    in_flight_statuses = [
        Order.Status.PENDING,
        Order.Status.STORE_PREPARING,
        Order.Status.DELIVERY_ASSIGNED,
        Order.Status.OTP_GENERATED,
    ]
    totals = window.aggregate(
        total=Count("id"),
        inFlight=Count("id", filter=Q(status__in=in_flight_statuses)),
        completed=Count("id", filter=Q(status=Order.Status.COMPLETED)),
        cancelled=Count("id", filter=Q(status=Order.Status.CANCELLED)),
        cod=Count("id", filter=Q(payment_method=Order.PaymentMethod.COD)),
    )
    rows = (
        window.select_related("user", "store", "store__profile")
        .prefetch_related("user__vehicles")
        .order_by("-created_at")[:100]
    )
    data = [
        {
            "id": str(order.id),
            "referenceCode": order.reference_code or "",
            "storeName": order.store.name,
            "storeCode": _store_code(order.store),
            "customerName": _customer_display(order.user),
            "customerPhone": _derive_phone(order.user.email),
            "city": order.city or "",
            "status": order.status,
            "paymentMethod": order.payment_method,
            "paymentStatus": order.payment_status,
            "totalSar": float(order.grand_total_sar),
            "createdAt": order.created_at.isoformat(),
            "captain": None,
        }
        for order in rows
    ]
    return Response({"days": days, "totals": totals, "rows": data})


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_delivery_matrix(request):
    cities = KsaDelivery.list()
    stores = (
        AuthStore.objects.select_related("profile").all().order_by("name")
    )
    fees_by_store = {}
    for fee in StoreDeliveryFee.objects.all():
        fees_by_store.setdefault(str(fee.store_id), {})[fee.city] = float(fee.fee)
    return Response(
        {
            "cities": cities,
            "stores": [
                {
                    "id": str(store.id),
                    "name": store.name,
                    "code": _store_code(store),
                    "city": (getattr(store, "profile", None).city if getattr(store, "profile", None) else "") or "",
                }
                for store in stores
            ],
            "fees": fees_by_store,
        }
    )


VALID_FEE_MAX = Decimal("9999.99")


@api_view(["PATCH"])
@permission_classes([AllowAny])
def admin_delivery_fee(request):
    store_id = request.data.get("storeId")
    city = (request.data.get("city") or "").strip().lower()
    raw_fee = request.data.get("feeSar", None)

    try:
        store = AuthStore.objects.get(id=store_id)
    except (AuthStore.DoesNotExist, ValueError, TypeError):
        return Response({"detail": "Store not found."}, status=status.HTTP_404_NOT_FOUND)

    if not KsaDelivery.is_valid(city):
        return Response(
            {"detail": "Unsupported city."}, status=status.HTTP_400_BAD_REQUEST
        )

    if raw_fee is None or raw_fee == "":
        StoreDeliveryFee.objects.filter(store=store, city=city).delete()
        return Response({"storeId": str(store.id), "city": city, "feeSar": None})

    try:
        fee = Decimal(str(raw_fee)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Response(
            {"detail": "feeSar must be a number."}, status=status.HTTP_400_BAD_REQUEST
        )
    if fee < 0 or fee > VALID_FEE_MAX:
        return Response(
            {"detail": "feeSar must be between 0 and 9999.99."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    obj, _created = StoreDeliveryFee.objects.update_or_create(
        store=store, city=city, defaults={"fee": fee}
    )
    return Response({"storeId": str(store.id), "city": city, "feeSar": float(obj.fee)})


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_financials_payouts(request):
    commission_rate = PlatformCommissionRate.get_rate()
    stores = AuthStore.objects.select_related("profile").all()

    store_totals = {
        row["store_id"]: row
        for row in Order.objects.filter(status=Order.Status.COMPLETED)
        .values("store_id")
        .annotate(gross=Sum("grand_total_sar"), orders=Count("id"))
    }

    latest_payouts = {}
    payout_totals = {}
    for payout in StorePayoutRequest.objects.order_by("-requested_at"):
        latest_payouts.setdefault(payout.store_id, payout)
        bucket = payout_totals.setdefault(
            payout.status, {"amount": 0.0, "count": 0}
        )
        bucket["amount"] += float(payout.amount_sar or 0)
        bucket["count"] += 1

    paid_total = payout_totals.get(StorePayoutRequest.Status.PAID, {}).get("amount", 0.0)
    pending_total = sum(
        payout_totals.get(status, {}).get("amount", 0.0)
        for status in (
            StorePayoutRequest.Status.PENDING_REVIEW,
            StorePayoutRequest.Status.PROCESSING,
        )
    )

    rows = []
    total_gmv = 0.0
    total_commission = 0.0
    for store in stores:
        row = store_totals.get(store.id)
        sales = round(float(row["gross"] or 0), 2) if row else 0.0
        orders_count = int(row["orders"] or 0) if row else 0
        commission = round(sales * commission_rate, 2)
        net = round(sales - commission, 2)
        payout_request = latest_payouts.get(store.id)
        profile = getattr(store, "profile", None)
        total_gmv += sales
        total_commission += commission
        rows.append(
            {
                "id": str(store.id),
                "storeId": str(store.id),
                "storeCode": _store_code(store),
                "storeName": store.name,
                "city": (profile.city if profile else "") or "",
                "salesSar": sales,
                "commissionSar": commission,
                "netSar": net,
                "ordersCount": orders_count,
                "payoutStatus": payout_request.status if payout_request else "none",
                "status": "paid"
                if (payout_request and payout_request.status == StorePayoutRequest.Status.PAID)
                else "pending",
            }
        )

    total_gmv = round(total_gmv, 2)
    total_commission = round(total_commission, 2)
    summary = {
        "totalGmvSar": total_gmv,
        "commissionSar": total_commission,
        "pendingPayoutSar": round(float(pending_total), 2),
        "completedPayoutSar": round(float(paid_total), 2),
        "blendedTakeRate": (total_commission / total_gmv) if total_gmv else 0.0,
        "commissionRate": commission_rate,
    }
    vat = {
        "storeSalesVatSar": round((total_gmv - total_commission) * 0.15, 2),
        "platformFeeVatSar": round(total_commission * 0.15, 2),
    }
    return Response({"summary": summary, "vat": vat, "rows": rows})


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_financials_accounting(request):
    period = request.query_params.get("period", "month")
    return Response(accounting_payload(period))


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_financials_expenses(request):
    return Response([])


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_financials_payment_accounts(request):
    settings_rows = StoreSettings.objects.select_related("store").all()
    data = [
        {
            "id": str(setting.id),
            "type": "bank_account",
            "name": {"ar": setting.store.name, "en": setting.store.name},
            "holder": {
                "ar": setting.account_holder_name,
                "en": setting.account_holder_name,
            },
            "reference": setting.iban,
            "status": "connected" if setting.stripe_connected else "disconnected",
            "isDefault": True,
            "updatedAt": setting.updated_at.isoformat(),
        }
        for setting in settings_rows
    ]
    return Response(data)


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_financials_payout_requests(request):
    payouts = StorePayoutRequest.objects.select_related("store").all()
    data = [
        {
            "id": str(payout.id),
            "storeId": str(payout.store_id),
            "storeCode": _store_code(payout.store),
            "storeName": {"ar": payout.store.name, "en": payout.store.name},
            "amountSar": float(payout.amount_sar),
            "stripeTransferId": payout.gateway_transaction_id,
            "requestedAt": payout.requested_at.isoformat(),
            "status": payout.status,
        }
        for payout in payouts
    ]
    return Response(data)


@api_view(["POST"])
@permission_classes([AllowAny])
def admin_financials_payouts_process(request):
    """Manually trigger the store payout processing task."""
    _require_platform_admin(request)

    result = process_store_payouts.delay()
    return Response(
        {
            "taskId": result.id,
            "status": "queued",
            "message": "Payout processing task has been queued.",
        }
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_financials_transactions(request):
    qs = (
        TransactionLedger.objects.select_related("order", "store", "order__user")
        .order_by("-created_at")
    )

    store_id = request.query_params.get("store")
    if store_id:
        qs = qs.filter(store_id=store_id)

    status = request.query_params.get("status")
    if status:
        qs = qs.filter(payout_status=status)

    from_date = request.query_params.get("from")
    if from_date:
        qs = qs.filter(created_at__date__gte=from_date)

    to_date = request.query_params.get("to")
    if to_date:
        qs = qs.filter(created_at__date__lte=to_date)

    data = []
    for ledger in qs[:500]:
        order = ledger.order
        user = order.user
        data.append(
            {
                "id": str(ledger.id),
                "orderId": str(order.id),
                "referenceCode": order.reference_code or "",
                "storeId": str(ledger.store_id),
                "storeCode": _store_code(ledger.store),
                "storeName": ledger.store.name,
                "customerName": _customer_display(user),
                "customerPhone": _derive_phone(user.email),
                "totalSar": round(float(ledger.total_amount or 0), 2),
                "commissionSar": round(float(ledger.commission_amount or 0), 2),
                "netPayoutSar": round(float(ledger.store_payout_amount or 0), 2),
                "commissionRate": round(float(ledger.commission_percent or 0) * 100, 4),
                "payoutStatus": ledger.payout_status,
                "paymentMethod": order.payment_method,
                "paymentStatus": order.payment_status,
                "gatewayTransactionId": ledger.gateway_transaction_id or "",
                "createdAt": ledger.created_at.isoformat() if ledger.created_at else "",
                "paidAt": ledger.paid_at.isoformat() if ledger.paid_at else "",
                "payoutDueAt": (
                    ledger.payout_due_at.isoformat() if ledger.payout_due_at else ""
                ),
            }
        )
    return Response(data)


def _serialize_accrual(adj):
    return {
        "id": str(adj.id),
        "storeId": str(adj.store_id),
        "storeCode": _store_code(adj.store),
        "storeName": adj.store.name,
        "orderId": str(adj.order_id) if adj.order_id else None,
        "referenceCode": adj.order.reference_code if adj.order else "",
        "type": adj.type,
        "amountSar": round(float(adj.amount_sar or 0), 2),
        "status": adj.status,
        "notes": adj.notes or "",
        "createdAt": adj.created_at.isoformat() if adj.created_at else "",
        "appliedAt": adj.applied_at.isoformat() if adj.applied_at else "",
    }


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def admin_financials_accruals(request):
    if request.method == "GET":
        qs = AccrualAdjustment.objects.select_related("store", "order").order_by(
            "-created_at"
        )
        store_id = request.query_params.get("store")
        if store_id:
            qs = qs.filter(store_id=store_id)
        adj_type = request.query_params.get("type")
        if adj_type:
            qs = qs.filter(type=adj_type)
        status_param = request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        from_date = request.query_params.get("from")
        if from_date:
            qs = qs.filter(created_at__date__gte=from_date)
        to_date = request.query_params.get("to")
        if to_date:
            qs = qs.filter(created_at__date__lte=to_date)
        return Response([_serialize_accrual(adj) for adj in qs[:500]])

    # POST create
    store_id = request.data.get("storeId")
    adj_type = request.data.get("type")
    raw_amount = request.data.get("amountSar")
    notes = request.data.get("notes", "") or ""
    order_id = request.data.get("orderId")

    if not store_id or not adj_type or raw_amount is None:
        return Response(
            {"detail": "storeId, type, and amountSar are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if adj_type not in dict(AccrualAdjustment.Type.choices):
        return Response(
            {"detail": "Invalid type."}, status=status.HTTP_400_BAD_REQUEST
        )

    try:
        store = AuthStore.objects.get(id=store_id)
    except (AuthStore.DoesNotExist, ValueError, TypeError):
        return Response({"detail": "Store not found."}, status=status.HTTP_404_NOT_FOUND)

    try:
        amount = Decimal(str(raw_amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Response(
            {"detail": "amountSar must be a number."}, status=status.HTTP_400_BAD_REQUEST
        )

    order = None
    if order_id:
        try:
            order = Order.objects.get(id=order_id)
        except (Order.DoesNotExist, ValueError, TypeError):
            return Response(
                {"detail": "Order not found."}, status=status.HTTP_404_NOT_FOUND
            )

    adj = AccrualAdjustment.objects.create(
        store=store,
        order=order,
        type=adj_type,
        amount_sar=amount,
        notes=notes,
    )
    return Response(_serialize_accrual(adj), status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([AllowAny])
def admin_financials_accrual_detail(request, adjustment_id):
    try:
        adj = AccrualAdjustment.objects.select_related("store", "order").get(
            id=adjustment_id
        )
    except (AccrualAdjustment.DoesNotExist, ValueError, TypeError):
        return Response({"detail": "Adjustment not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        adj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    new_status = request.data.get("status")
    if new_status is not None and new_status not in dict(
        AccrualAdjustment.Status.choices
    ):
        return Response(
            {"detail": "Invalid status."}, status=status.HTTP_400_BAD_REQUEST
        )

    if new_status is not None:
        adj.status = new_status
        if new_status == AccrualAdjustment.Status.APPLIED and not adj.applied_at:
            adj.applied_at = timezone.now()
        elif new_status != AccrualAdjustment.Status.APPLIED:
            adj.applied_at = None

    raw_amount = request.data.get("amountSar")
    if raw_amount is not None:
        try:
            adj.amount_sar = Decimal(str(raw_amount)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            return Response(
                {"detail": "amountSar must be a number."}, status=status.HTTP_400_BAD_REQUEST
            )

    notes = request.data.get("notes")
    if notes is not None:
        adj.notes = notes

    adj.save()
    return Response(_serialize_accrual(adj))


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_financials_commission_rate_log(request):
    obj = PlatformCommissionRate.objects.first()
    if obj is None:
        return Response([])
    return Response(
        [
            {
                "id": str(obj.id),
                "at": obj.updated_at.isoformat(),
                "toPct": round(float(obj.rate) * 100, 4),
                "updatedBy": obj.updated_by,
            }
        ]
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_financials_commission_rate_log(request):
    obj = PlatformCommissionRate.objects.first()
    if obj is None:
        return Response([])
    return Response(
        [
            {
                "id": str(obj.id),
                "at": obj.updated_at.isoformat(),
                "toPct": round(float(obj.rate) * 100, 4),
                "updatedBy": obj.updated_by,
            }
        ]
    )


@api_view(["GET", "PATCH"])
@permission_classes([AllowAny])
def admin_financials_platform_account(request):
    obj = PlatformGatewayConfig.get_singleton()

    if request.method == "GET":
        return Response(
            {
                "id": str(obj.id),
                "bankName": obj.bank_name,
                "accountHolderName": obj.account_holder_name,
                "iban": obj.iban,
                "swiftBic": obj.swift_bic,
                "madaMerchantId": obj.mada_merchant_id,
                "acquirerApiKey": "***redacted***" if obj.acquirer_api_key else "",
                "acquirerApiSecret": "***redacted***" if obj.acquirer_api_secret else "",
                "supportedCardSchemes": obj.supported_card_schemes,
                "isVerified": obj.is_verified,
                "createdAt": obj.created_at.isoformat() if obj.created_at else "",
                "updatedAt": obj.updated_at.isoformat() if obj.updated_at else "",
            }
        )

    # PATCH update
    bank_name = request.data.get("bankName", obj.bank_name)
    account_holder_name = request.data.get("accountHolderName", obj.account_holder_name)
    iban = request.data.get("iban", obj.iban)
    swift_bic = request.data.get("swiftBic", obj.swift_bic)
    mada_merchant_id = request.data.get("madaMerchantId", obj.mada_merchant_id)
    supported_card_schemes = request.data.get(
        "supportedCardSchemes", obj.supported_card_schemes
    )
    is_verified = request.data.get("isVerified", obj.is_verified)

    obj.bank_name = bank_name or ""
    obj.account_holder_name = account_holder_name or ""
    obj.iban = iban or ""
    obj.swift_bic = swift_bic or ""
    obj.mada_merchant_id = mada_merchant_id or ""
    obj.supported_card_schemes = (
        supported_card_schemes if isinstance(supported_card_schemes, list) else []
    )
    obj.is_verified = bool(is_verified)

    acquirer_api_key = request.data.get("acquirerApiKey")
    if acquirer_api_key is not None:
        obj.acquirer_api_key = acquirer_api_key

    acquirer_api_secret = request.data.get("acquirerApiSecret")
    if acquirer_api_secret is not None:
        obj.acquirer_api_secret = acquirer_api_secret

    obj.save()

    return Response(
        {
            "id": str(obj.id),
            "bankName": obj.bank_name,
            "accountHolderName": obj.account_holder_name,
            "iban": obj.iban,
            "swiftBic": obj.swift_bic,
            "madaMerchantId": obj.mada_merchant_id,
            "acquirerApiKey": "***redacted***" if obj.acquirer_api_key else "",
            "acquirerApiSecret": "***redacted***" if obj.acquirer_api_secret else "",
            "supportedCardSchemes": obj.supported_card_schemes,
            "isVerified": obj.is_verified,
            "createdAt": obj.created_at.isoformat() if obj.created_at else "",
            "updatedAt": obj.updated_at.isoformat() if obj.updated_at else "",
        }
    )


ROLE_LABELS = {
    User.Role.CUSTOMER: {"ar": "عميل", "en": "Customer"},
    User.Role.STORE_ADMIN: {"ar": "مسؤول متجر", "en": "Store Admin"},
    User.Role.STORE_MANAGER: {"ar": "مدير متجر", "en": "Store Manager"},
    User.Role.DRIVER: {"ar": "سائق", "en": "Driver"},
}


@api_view(["GET"])
@permission_classes([AllowAny])
def admin_profile(request):
    user = User.objects.filter(is_staff=True).first() or User.objects.first()
    if not user:
        return Response(
            {
                "id": "",
                "fullName": "",
                "email": "",
                "phone": "",
                "role": {"ar": "مدير النظام", "en": "System Administrator"},
                "avatarUrl": "",
                "isActive": False,
                "createdAt": "",
            }
        )
    _role_payload = (
        {"ar": "مدير النظام", "en": "System Administrator"}
        if user.is_superuser
        else ROLE_LABELS.get(user.role, {"ar": user.role, "en": user.role})
    )
    return Response(
        {
            "id": str(user.id),
            "fullName": user.full_name,
            "email": user.email,
            "phone": _derive_phone(user.email),
            "role": _role_payload,
            "avatarUrl": user.avatar_url or "",
            "isActive": user.is_active,
            "createdAt": user.created_at.isoformat().replace("+00:00", "Z"),
        }
    )
