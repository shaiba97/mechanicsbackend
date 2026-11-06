from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.models import AuthStore
from catalog.models import Car
from inventory.models import InventoryItem
from store_ops.broadcast import broadcast_order_new

from .models import CustomerVehicle, Order, Review
from .serializers import (
    CheckoutSerializer,
    CustomerVehicleSerializer,
    CreateCustomerVehicleSerializer,
    OrderSerializer,
)



def _serialize_part(item):
    return {
        "id": str(item.pk),
        "sku": item.part_number,
        "name": {
            "ar": item.part_name_ar,
            "en": item.part_name_en,
        },
        "brandId": str(item.brand_id) if item.brand_id else "",
        "brandName": item.brand.name if item.brand_id else None,
        "categoryId": str(item.category_id) if item.category_id else "",
        "categoryName": item.category.name if item.category_id else None,
        "storeId": str(item.store_id) if item.store_id else "",
        "storeName": item.store.name if item.store_id else None,
        "priceSar": float(item.price),
        "stockQty": item.quantity,
        "condition": item.condition,
        "imageUrl": item.image_url,
        "isActive": item.is_active,
        "compatibilityVins": item.compatibility_vins or [],
    }


def _require_user(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied("Authentication required.")
    return user


@api_view(["GET"])
@permission_classes([AllowAny])
def customer_parts_search(request):
    """Search parts by VIN, make, year, or category."""
    vin = request.query_params.get("vin", "").strip()
    make = request.query_params.get("make", "").strip()
    year = request.query_params.get("year", "").strip()
    category_id = request.query_params.get("categoryId", "").strip()

    queryset = InventoryItem.objects.filter(is_active=True, quantity__gt=0).select_related(
        "category", "brand", "store"
    )

    if vin:
        queryset = queryset.filter(compatibility_vins__icontains=vin)
    else:
        if make:
            queryset = queryset.filter(brand__code__iexact=make)
        if year:
            try:
                y = int(year)
                car_brands = Car.objects.filter(year=y)
                if make:
                    car_brands = car_brands.filter(brand__code__iexact=make)
                brand_ids = car_brands.values_list("brand_id", flat=True).distinct()
                queryset = queryset.filter(brand_id__in=brand_ids)
            except ValueError:
                pass
        if category_id:
            queryset = queryset.filter(category_id=category_id)

    data = [_serialize_part(item) for item in queryset[:50]]

    return Response({"results": data, "count": len(data)})


@api_view(["GET"])
@permission_classes([AllowAny])
def customer_part_detail(request, part_id):
    try:
        item = InventoryItem.objects.select_related(
            "category", "brand", "store"
        ).get(pk=part_id, is_active=True, quantity__gt=0)
    except InventoryItem.DoesNotExist:
        return Response({"detail": "Part not found."}, status=status.HTTP_404_NOT_FOUND)
    return Response(_serialize_part(item))


class CustomerVehicleListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _require_user(request)
        vehicles = CustomerVehicle.objects.filter(user=request.user)
        serializer = CustomerVehicleSerializer(vehicles, many=True)
        return Response(serializer.data)

    def post(self, request):
        _require_user(request)
        serializer = CreateCustomerVehicleSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        if data.get("is_primary"):
            CustomerVehicle.objects.filter(user=request.user).update(is_primary=False)

        vehicle = CustomerVehicle.objects.create(
            user=request.user,
            nickname=data.get("nickname", ""),
            vin=data.get("vin", ""),
            make=data.get("make", ""),
            model=data.get("model", ""),
            year=data.get("year"),
            is_primary=data.get("is_primary", False),
        )
        return Response(CustomerVehicleSerializer(vehicle).data, status=status.HTTP_201_CREATED)


class CustomerVehicleDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, vehicle_id):
        _require_user(request)
        try:
            vehicle = CustomerVehicle.objects.get(id=vehicle_id, user=request.user)
        except CustomerVehicle.DoesNotExist:
            return Response({"detail": "Vehicle not found."}, status=status.HTTP_404_NOT_FOUND)
        vehicle.delete()
        return Response({"detail": "Vehicle deleted."}, status=status.HTTP_204_NO_CONTENT)


class OrderListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _require_user(request)
        orders = Order.objects.filter(user=request.user).select_related("store").prefetch_related("items__inventory_item")
        serializer = OrderSerializer(orders, many=True)
        return Response(serializer.data)


class OrderDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, order_id):
        _require_user(request)
        try:
            order = (
                Order.objects.select_related("store")
                .prefetch_related("items__inventory_item")
                .get(id=order_id, user=request.user)
            )
        except Order.DoesNotExist:
            return Response({"detail": "Order not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = OrderSerializer(order)
        return Response(serializer.data)


class CheckoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        _require_user(request)
        serializer = CheckoutSerializer(data=request.data, context={"user": request.user})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        store = AuthStore.objects.get(id=data["store_id"])
        city = data["city"]
        street = data["street"].strip()
        payment_method = data["payment_method"]

        vehicle = None
        vehicle_id = data.get("vehicle_id")
        if vehicle_id:
            try:
                vehicle = CustomerVehicle.objects.get(id=vehicle_id, user=request.user)
            except (CustomerVehicle.DoesNotExist, ValueError, TypeError):
                vehicle = None
        else:
            vehicle = (
                CustomerVehicle.objects.filter(user=request.user, is_primary=True)
                .first()
            )

        from .delivery import KsaDelivery
        delivery_fee = KsaDelivery.fee(city)

        # Override with store‑specific fee if configured
        from store_ops.models import StoreDeliveryFee
        store_fee = StoreDeliveryFee.objects.filter(store=store, city=city).first()
        if store_fee:
            delivery_fee = store_fee.fee

        with transaction.atomic():
            total = 0
            order_items_data = []
            for item_data in data["items"]:
                try:
                    inventory_item = InventoryItem.objects.select_for_update().get(
                        id=item_data["inventory_item_id"], is_active=True
                    )
                except InventoryItem.DoesNotExist:
                    return Response(
                        {"detail": f"Item {item_data['inventory_item_id']} not found or inactive."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                qty = int(item_data["quantity"])
                if inventory_item.quantity < qty:
                    return Response(
                        {"detail": f"Insufficient stock for {inventory_item.part_number}."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if inventory_item.store_id != store.id:
                    return Response(
                        {"detail": f"Item {inventory_item.part_number} is not sold by this store."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                unit_price = inventory_item.price
                line_total = unit_price * qty
                total += line_total
                order_items_data.append({
                    "inventory_item": inventory_item,
                    "quantity": qty,
                    "unit_price_sar": unit_price,
                    "line_total_sar": line_total,
                })

            order = Order.objects.create(
                user=request.user,
                store=store,
                vehicle=vehicle,
                total_sar=total,
                delivery_fee_sar=delivery_fee,
                grand_total_sar=total + delivery_fee,
                city=city,
                street=street,
                payment_method=payment_method,
                status=Order.Status.PENDING,
                payment_status=Order.PaymentStatus.UNPAID,
            )
            for oid in order_items_data:
                OrderItem.objects.create(
                    order=order,
                    inventory_item=oid["inventory_item"],
                    quantity=oid["quantity"],
                    unit_price_sar=oid["unit_price_sar"],
                    line_total_sar=oid["line_total_sar"],
                )

        # Broadcast new order to the store's dashboard
        payload = {
            "id": str(order.id),
            "status": order.status,
            "totalSar": float(order.total_sar),
            "deliveryFeeSar": float(delivery_fee),
            "grandTotalSar": float(order.grand_total_sar),
            "paymentMethod": order.payment_method,
            "city": order.city,
            "street": order.street,
            "items": [
                {
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
        }
        broadcast_order_new(store.id, payload)

        if payment_method == Order.PaymentMethod.COD:
            return Response({
                "orderId": str(order.id),
                "paymentMethod": "cod",
                "deliveryFeeSar": float(delivery_fee),
                "totalSar": float(total),
                "grandTotalSar": float(order.grand_total_sar),
            }, status=status.HTTP_201_CREATED)

        if payment_method == Order.PaymentMethod.MOYASAR:
            # Mada payment via Moyasar/HyperPay gateway
            try:
                # Call Mada gateway API to create payment session
                import requests
                import json
                
                moyasar_api_key = getattr(settings, 'MOYASAR_API_KEY', '')
                moyasar_base_url = "https://api.moyasarpayments.com/v1"
                
                payment_data = {
                    "amount": int(order.grand_total_sar * 100),  # Amount in halalas
                    "currency": "SAR",
                    "description": f"Order {order.id}",
                    "order_id": str(order.id),
                    "callback_url": f"{settings.BASE_URL}/api/store/payments/mada/webhook",
                    "metadata": {
                        "order_id": str(order.id),
                        "store_id": str(store.id),
                        "user_id": str(request.user.id),
                    },
                }
                
                resp = requests.post(
                    f"{moyasar_base_url}/payments",
                    json=payment_data,
                    headers={
                        "Authorization": f"Bearer {moyasar_api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=30,
                )
                
                if resp.status_code == 200:
                    mada_data = resp.json()
                    order.checkout_session_id = mada_data.get("id", "")
                    order.save(update_fields=["checkout_session_id"])
                    
                    return Response({
                        "orderId": str(order.id),
                        "clientSecret": mada_data.get("id", ""),
                        "paymentUrl": mada_data.get("payment_url", ""),
                        "totalSar": float(total),
                        "grandTotalSar": float(order.grand_total_sar),
                        "deliveryFeeSar": float(delivery_fee),
                    }, status=status.HTTP_201_CREATED)
                else:
                    order.delete()
                    return Response(
                        {"detail": f"Mada payment initiation failed: {resp.text}"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            except Exception as exc:
                order.delete()
                return Response(
                    {"detail": f"Mada payment error: {str(exc)}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        return Response(
            {"detail": f"Unsupported payment method: {payment_method}."},
            status=status.HTTP_400_BAD_REQUEST,
        )


class ReviewSerializer(serializers.ModelSerializer):
    id = serializers.SerializerMethodField()
    order_item_id = serializers.SerializerMethodField()
    user_id = serializers.SerializerMethodField()
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = Review
        fields = ["id", "order_item_id", "user_id", "user_name", "rating", "comment", "created_at"]
        read_only_fields = fields

    def get_id(self, obj):
        return str(obj.pk)

    def get_order_item_id(self, obj):
        return str(obj.order_item_id)

    def get_user_id(self, obj):
        return str(obj.user_id)

    def get_user_name(self, obj):
        return obj.user.full_name or (obj.user.email if obj.user.email.isdigit() else None) or "Customer"


@api_view(["GET"])
@permission_classes([AllowAny])
def delivery_cities(request):
    from .delivery import KsaDelivery
    from store_ops.models import StoreDeliveryFee

    store_id = request.query_params.get("storeId")
    cities = KsaDelivery.list()
    if store_id:
        try:
            from uuid import UUID
            UUID(store_id)  # validate uuid format
            configured = {
                fee.city: fee.fee
                for fee in StoreDeliveryFee.objects.filter(store_id=store_id)
            }
            for city in cities:
                slug = city["slug"]
                if slug in configured:
                    city["fee"] = float(configured[slug])
        except (ValueError, Exception):
            pass  # ignore invalid storeId
    return Response({"cities": cities})


@api_view(["GET"])
@permission_classes([AllowAny])
def part_reviews(request, part_id):
    all_reviews = Review.objects.filter(order_item__inventory_item_id=part_id)
    count = all_reviews.count()
    reviews = all_reviews.select_related("user").order_by("-created_at")[:50]
    serialized = ReviewSerializer(reviews, many=True).data
    average = round(sum(r["rating"] for r in serialized) / count, 1) if count else None
    return Response({"results": serialized, "count": count, "average": average})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_review(request):
    _require_user(request)
    payload = request.data
    if not isinstance(payload, dict):
        return Response({"detail": "Invalid body."}, status=status.HTTP_400_BAD_REQUEST)

    order_item_id = str(payload.get("orderItemId") or "").strip()
    rating_raw = payload.get("rating")
    comment = str(payload.get("comment") or "").strip()

    try:
        rating = int(rating_raw)
    except (TypeError, ValueError):
        return Response({"detail": "Rating must be a number between 1 and 5."}, status=status.HTTP_400_BAD_REQUEST)
    if not 1 <= rating <= 5:
        return Response({"detail": "Rating must be between 1 and 5."}, status=status.HTTP_400_BAD_REQUEST)
    if len(comment) > 1000:
        return Response({"detail": "Comment is too long."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        order_item = (
            OrderItem.objects.select_related("order", "inventory_item")
            .get(id=order_item_id, order__user=request.user)
        )
    except (OrderItem.DoesNotExist, ValueError):
        return Response({"detail": "Order item not found."}, status=status.HTTP_404_NOT_FOUND)

    if order_item.order.status != Order.Status.COMPLETED:
        return Response(
            {"detail": "You can review a product only after the order is completed."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    existing = Review.objects.filter(user=request.user, order_item=order_item).first()
    if existing:
        existing.rating = rating
        existing.comment = comment
        existing.save(update_fields=["rating", "comment"])
        return Response(ReviewSerializer(existing).data)

    review = Review.objects.create(user=request.user, order_item=order_item, rating=rating, comment=comment)
    return Response(ReviewSerializer(review).data, status=status.HTTP_201_CREATED)
