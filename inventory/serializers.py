from rest_framework import serializers

from catalog.models import Brand, Category

from .models import InventoryItem


def _valid_bilingual(value):
    if not isinstance(value, dict):
        return False
    for lang in ('ar', 'en'):
        text = value.get(lang)
        if not isinstance(text, str) or not text.strip():
            return False
    return True


class InventoryItemSerializer(serializers.ModelSerializer):
    id = serializers.SerializerMethodField()
    storeId = serializers.SerializerMethodField()
    partNameAr = serializers.CharField(source='part_name_ar', max_length=200)
    partNameEn = serializers.CharField(source='part_name_en', max_length=200)
    partNumber = serializers.CharField(source='part_number', max_length=100)
    categoryId = serializers.PrimaryKeyRelatedField(
        source='category',
        queryset=Category.objects.all(),
        allow_null=True,
        required=False,
    )
    brandId = serializers.PrimaryKeyRelatedField(
        source='brand',
        queryset=Brand.objects.all(),
        allow_null=True,
        required=False,
    )
    categoryName = serializers.SerializerMethodField()
    brandName = serializers.SerializerMethodField()
    minStockAlert = serializers.IntegerField(source='min_stock_alert', min_value=0)
    compatibilityVins = serializers.JSONField(
        source='compatibility_vins', required=False, allow_null=True
    )
    imageUrl = serializers.URLField(
        source='image_url', required=False, allow_blank=True, max_length=500
    )
    isActive = serializers.BooleanField(source='is_active', required=False)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)

    class Meta:
        model = InventoryItem
        fields = [
            'id',
            'storeId',
            'partNameAr',
            'partNameEn',
            'partNumber',
            'categoryId',
            'categoryName',
            'brandId',
            'brandName',
            'condition',
            'price',
            'quantity',
            'minStockAlert',
            'compatibilityVins',
            'imageUrl',
            'isActive',
            'createdAt',
            'updatedAt',
        ]

    def get_id(self, obj):
        return str(obj.pk)

    def get_storeId(self, obj):
        return str(obj.store_id) if obj.store_id else None

    def get_categoryName(self, obj):
        return obj.category.name if obj.category_id else None

    def get_brandName(self, obj):
        return obj.brand.name if obj.brand_id else None

    def validate_partNameAr(self, value):
        cleaned = (value or '').strip()
        if not cleaned:
            raise serializers.ValidationError('Arabic part name is required.')
        return cleaned

    def validate_partNameEn(self, value):
        cleaned = (value or '').strip()
        if not cleaned:
            raise serializers.ValidationError('English part name is required.')
        return cleaned

    def validate_partNumber(self, value):
        cleaned = (value or '').strip()
        if not cleaned:
            raise serializers.ValidationError('Part number is required.')
        return cleaned

    def validate_price(self, value):
        if value is None or value < 0:
            raise serializers.ValidationError('Price must be zero or greater.')
        return value

    def validate_quantity(self, value):
        if value < 0:
            raise serializers.ValidationError('Quantity cannot be negative.')
        return value

    def validate_minStockAlert(self, value):
        if value < 0:
            raise serializers.ValidationError('Minimum stock alert cannot be negative.')
        return value

    def validate_compatibilityVins(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError('Compatibility must be a list.')
        cleaned = []
        for entry in value:
            if isinstance(entry, str):
                if not entry.strip():
                    raise serializers.ValidationError(
                        'Each compatibility entry must be a non-empty string.'
                    )
                cleaned.append(entry.strip())
                continue
            if isinstance(entry, dict):
                try:
                    year_from = int(entry.get('yearFrom'))
                    year_to = int(entry.get('yearTo'))
                except (TypeError, ValueError):
                    raise serializers.ValidationError(
                        'Compatibility years must be integers.'
                    )
                if not 1980 <= year_from <= 2030 or not year_from <= year_to <= 2030:
                    raise serializers.ValidationError(
                        'Compatibility years must satisfy 1980 <= start <= end <= 2030.'
                    )
                if not _valid_bilingual(entry.get('make')) or not _valid_bilingual(
                    entry.get('model')
                ):
                    raise serializers.ValidationError(
                        'Compatibility make and model must include ar and en.'
                    )
                cleaned.append(
                    {
                        'make': entry['make'],
                        'model': entry['model'],
                        'yearFrom': year_from,
                        'yearTo': year_to,
                    }
                )
                continue
            raise serializers.ValidationError(
                'Each compatibility entry must be a string or a compatibility object.'
            )
        return cleaned

    def validate(self, attrs):
        view = self.context.get('view')
        store = view.store() if view and hasattr(view, 'store') else None
        part_number = attrs.get('part_number')
        if part_number is None and self.instance is not None:
            part_number = self.instance.part_number
        if store is not None and part_number:
            existing = InventoryItem.objects.filter(
                store=store, part_number=part_number
            )
            if self.instance is not None:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise serializers.ValidationError(
                    {'partNumber': ['Part number already exists in this store.']}
                )
        return attrs

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['categoryId'] = (
            str(representation['categoryId']) if representation['categoryId'] else None
        )
        representation['brandId'] = (
            str(representation['brandId']) if representation['brandId'] else None
        )
        representation['price'] = float(representation['price'])
        return representation
