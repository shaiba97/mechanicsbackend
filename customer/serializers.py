from rest_framework import serializers

from authentication.models import AuthStore
from catalog.models import Brand, Car, Category
from inventory.models import InventoryItem

from .models import CustomerVehicle, Order, OrderItem


class CustomerVehicleSerializer(serializers.ModelSerializer):
    id = serializers.SerializerMethodField()

    class Meta:
        model = CustomerVehicle
        fields = [
            "id",
            "nickname",
            "vin",
            "make",
            "model",
            "year",
            "is_primary",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_id(self, obj):
        return str(obj.pk)


class CreateCustomerVehicleSerializer(serializers.Serializer):
    nickname = serializers.CharField(max_length=100, required=False, allow_blank=True)
    vin = serializers.CharField(max_length=17, required=False, allow_blank=True)
    make = serializers.CharField(max_length=100, required=False, allow_blank=True)
    model = serializers.CharField(max_length=100, required=False, allow_blank=True)
    year = serializers.IntegerField(required=False, allow_null=True, min_value=1980, max_value=2030)
    is_primary = serializers.BooleanField(required=False, default=False)

    def validate_vin(self, value):
        value = (value or "").strip()
        if value and len(value) != 17:
            raise serializers.ValidationError("VIN must be exactly 17 characters.")
        return value


class OrderItemSerializer(serializers.ModelSerializer):
    id = serializers.SerializerMethodField()
    inventory_item_id = serializers.SerializerMethodField()
    part_number = serializers.SerializerMethodField()
    part_name = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "inventory_item_id",
            "part_number",
            "part_name",
            "quantity",
            "unit_price_sar",
            "line_total_sar",
        ]
        read_only_fields = ["id", "line_total_sar"]

    def get_id(self, obj):
        return str(obj.pk)

    def get_inventory_item_id(self, obj):
        return str(obj.inventory_item_id)

    def get_part_number(self, obj):
        return obj.inventory_item.part_number

    def get_part_name(self, obj):
        return obj.inventory_item.part_name_en or obj.inventory_item.part_name_ar


class OrderSerializer(serializers.ModelSerializer):
    id = serializers.SerializerMethodField()
    store_name = serializers.SerializerMethodField()
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "store_name",
            "status",
            "payment_status",
            "total_sar",
            "delivery_fee_sar",
            "grand_total_sar",
            "city",
            "street",
            "payment_method",
            "handover_otp",
            "created_at",
            "updated_at",
            "items",
        ]
        read_only_fields = [
            "id",
            "store_name",
            "status",
            "payment_status",
            "total_sar",
            "delivery_fee_sar",
            "grand_total_sar",
            "city",
            "street",
            "payment_method",
            "handover_otp",
            "created_at",
            "updated_at",
            "items",
        ]

    def get_id(self, obj):
        return str(obj.pk)

    def get_store_name(self, obj):
        return obj.store.name


class CheckoutSerializer(serializers.Serializer):
    PAYMENT_METHODS = ("moyasar", "cod")

    items = serializers.ListField(
        child=serializers.DictField(),
        required=True,
        min_length=1,
    )
    store_id = serializers.UUIDField(required=True)
    vehicle_id = serializers.UUIDField(required=False, allow_null=True)
    city = serializers.CharField(max_length=50, required=True)
    street = serializers.CharField(max_length=200, required=True)
    payment_method = serializers.ChoiceField(choices=PAYMENT_METHODS, required=True)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("At least one item is required.")
        for item in value:
            if "inventory_item_id" not in item:
                raise serializers.ValidationError("Each item must have inventory_item_id.")
            if "quantity" not in item:
                raise serializers.ValidationError("Each item must have quantity.")
            try:
                qty = int(item["quantity"])
                if qty < 1:
                    raise serializers.ValidationError("Quantity must be at least 1.")
            except (TypeError, ValueError):
                raise serializers.ValidationError("Quantity must be a valid integer.")
        return value

    def validate_store_id(self, value):
        try:
            AuthStore.objects.get(id=value)
        except AuthStore.DoesNotExist:
            raise serializers.ValidationError("Store not found.")
        return value

    def validate_city(self, value):
        slug = (value or "").strip().lower()
        from .delivery import KsaDelivery
        if not KsaDelivery.is_valid(slug):
            raise serializers.ValidationError("Delivery is only available in KSA cities. Please choose a supported city.")
        return slug

    def validate_vehicle_id(self, value):
        if value is None:
            return value
        user = self.context.get("user")
        if user is None:
            return value
        try:
            from .models import CustomerVehicle
            CustomerVehicle.objects.get(id=value, user=user)
        except CustomerVehicle.DoesNotExist:
            raise serializers.ValidationError("Vehicle not found or does not belong to you.")
        return value
