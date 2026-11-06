import uuid
from decimal import Decimal

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import IntegrityError, models
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework import serializers, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.http import FileResponse
from io import BytesIO
import base64
from uuid import UUID

from .models import InventoryItem, InventoryImage
from .serializers import InventoryItemSerializer


def broadcast_stock_change(store_id, part_id, quantity):
    channel_layer = get_channel_layer()
    if channel_layer is None or store_id is None:
        return
    async_to_sync(channel_layer.group_send)(
        f'store_{store_id}',
        {
            'type': 'inventory.update',
            'partId': str(part_id),
            'qty': int(quantity),
        },
    )


class InventoryImageUploadSerializer(serializers.Serializer):
    fileName = serializers.CharField(max_length=255, required=True)
    contentType = serializers.CharField(max_length=100, required=True)
    fileData = serializers.CharField(required=True, max_length=20_000_000)

    def validate_contentType(self, value):
        allowed = {"image/png", "image/jpeg", "image/webp"}
        if value not in allowed:
            raise serializers.ValidationError(
                f"Content type must be one of {', '.join(allowed)}."
            )
        return value

    def validate_fileData(self, value):
        try:
            raw = base64.b64decode(value, validate=True)
        except Exception:
            raise serializers.ValidationError("Invalid base64 file data.")
        if len(raw) > 5 * 1024 * 1024:
            raise serializers.ValidationError("File too large (max 5 MB).")
        return value


class InventoryItemViewSet(viewsets.ModelViewSet):
    serializer_class = InventoryItemSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']
    lookup_value_regex = '[0-9a-fA-F-]{36}'

    def store(self):
        return getattr(self.request.user, 'store', None)

    def _require_store(self):
        store = self.store()
        if store is None:
            raise PermissionDenied('Authenticated user is not linked to a store.')
        return store

    def get_queryset(self):
        store = self.store()
        if store is None:
            return InventoryItem.objects.none()
        queryset = InventoryItem.objects.filter(store=store).select_related(
            'category', 'brand'
        )
        params = self.request.query_params
        if query := params.get('q'):
            queryset = queryset.filter(
                models.Q(part_number__icontains=query)
                | models.Q(part_name_ar__icontains=query)
                | models.Q(part_name_en__icontains=query)
            )
        if category_id := params.get('categoryId'):
            queryset = queryset.filter(category_id=category_id)
        if condition := params.get('condition'):
            queryset = queryset.filter(condition=condition)
        if params.get('lowStock') in ('1', 'true'):
            queryset = queryset.filter(quantity__lte=models.F('min_stock_alert'))
        return queryset

    def perform_create(self, serializer):
        serializer.save(store=self._require_store())

    def _part_number_conflict(self):
        return serializers.ValidationError(
            {'partNumber': ['Part number already exists in this store.']}
        )

    def create(self, request, *args, **kwargs):
        try:
            return super().create(request, *args, **kwargs)
        except IntegrityError:
            raise self._part_number_conflict()

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        try:
            self.perform_update(serializer)
        except IntegrityError:
            raise self._part_number_conflict()
        if 'quantity' in serializer.validated_data:
            broadcast_stock_change(
                instance.store_id, instance.pk, instance.quantity
            )
        return Response(serializer.data)

    @action(detail=False, methods=['patch'], url_path='bulk-stock')
    def bulk_stock(self, request):
        store = self._require_store()
        items = request.data.get('items')
        if not isinstance(items, list) or not items:
            raise serializers.ValidationError(
                {
                    'items': [
                        'Provide a non-empty list of {id, quantity} objects.'
                    ]
                }
            )
        wanted = {}
        for entry in items:
            if not isinstance(entry, dict):
                raise serializers.ValidationError(
                    {'items': ['Each item must be an object with id and quantity.']}
                )
            try:
                item_id = uuid.UUID(str(entry.get('id', '')))
            except (ValueError, TypeError):
                raise serializers.ValidationError(
                    {'items': [f'Invalid part id: {entry.get("id")}']}
                )
            try:
                quantity = int(entry.get('quantity'))
            except (TypeError, ValueError):
                raise serializers.ValidationError(
                    {'items': [f'Invalid quantity for part {item_id}.']}
                )
            if quantity < 0:
                raise serializers.ValidationError(
                    {'items': ['Quantity cannot be negative.']}
                )
            wanted[item_id] = quantity

        found = {item.pk: item for item in InventoryItem.objects.filter(store=store, pk__in=wanted)}
        missing = [str(pk) for pk in wanted if pk not in found]
        if missing:
            raise serializers.ValidationError(
                {'items': [f'Parts not found in your store: {", ".join(missing)}']}
            )

        for item in found.values():
            quantity = wanted[item.pk]
            if item.quantity != quantity:
                item.quantity = quantity
                item.save(update_fields=['quantity', 'updated_at'])
                broadcast_stock_change(store.pk, item.pk, quantity)

        data = self.get_serializer(list(found.values()), many=True).data
        return Response({'updated': data, 'count': len(found)})


def inventory_image_upload(request):
    """POST /api/inventory/images — upload a new image for a store's inventory item."""
    if request.method != "POST":
        return Response({"detail": "POST only."}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    store = None
    from authentication.models import AuthStore

    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        store = getattr(user, "store", None)
        if not store:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("User is not linked to a store.")

    if not store:
        return Response({"detail": "Store context required."}, status=status.HTTP_400_BAD_REQUEST)

    serializer = InventoryImageUploadSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    data = serializer.validated_data
    file_data = base64.b64decode(data["fileData"], validate=True)
    content_type = data["contentType"]
    file_name = data.get("fileName") or "product-image"

    img = InventoryImage.objects.create(
        store=store,
        file_name=file_name,
        content_type=content_type,
        size=len(file_data),
        file_data=file_data,
    )

    return Response(
        {
            "id": str(img.pk),
            "url": f"/api/inventory/images/{img.pk}",
            "fileName": img.file_name,
            "contentType": img.content_type,
            "size": img.size,
        },
        status=status.HTTP_201_CREATED,
    )


def inventory_image_serve(request, image_id):
    """GET /api/inventory/images/<uuid:image_id> — serve image publicly (AllowAny)."""
    img = get_object_or_404(InventoryImage, pk=image_id)
    response = FileResponse(BytesIO(img.file_data), content_type=img.content_type)
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    response["Content-Disposition"] = f'inline; filename="{img.file_name}"'
    return response