from rest_framework import serializers

from .models import Brand, Car, Category


def validate_bilingual(value, field_label):
    if not isinstance(value, dict):
        raise serializers.ValidationError(
            f"{field_label} must be an object with ar and en keys."
        )
    for lang in ("ar", "en"):
        text = value.get(lang)
        if not isinstance(text, str) or not text.strip():
            raise serializers.ValidationError(
                f"Both Arabic and English {field_label} are required."
            )
    return value


class BrandSerializer(serializers.ModelSerializer):
    id = serializers.SerializerMethodField()
    logoUrl = serializers.URLField(
        source="logo_url", required=False, allow_blank=True, max_length=500
    )

    class Meta:
        model = Brand
        fields = ["id", "code", "name", "logoUrl", "active"]
        extra_kwargs = {"name": {"required": True}, "active": {"required": False}}

    def get_id(self, obj):
        return str(obj.pk)

    def validate_name(self, value):
        return validate_bilingual(value, "name")


class CarSerializer(serializers.ModelSerializer):
    id = serializers.SerializerMethodField()
    model = serializers.JSONField(source="model_name")
    brandId = serializers.PrimaryKeyRelatedField(
        queryset=Brand.objects.all(), source="brand"
    )

    class Meta:
        model = Car
        fields = ["id", "model", "year", "brandId", "active"]
        extra_kwargs = {
            "year": {"min_value": 1980, "max_value": 2030},
            "active": {"required": False},
        }

    def get_id(self, obj):
        return str(obj.pk)

    def validate_model(self, value):
        return validate_bilingual(value, "model")

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation["brandId"] = str(representation["brandId"])
        return representation


class CategorySerializer(serializers.ModelSerializer):
    id = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ["id", "name", "active"]
        extra_kwargs = {"name": {"required": True}, "active": {"required": False}}

    def get_id(self, obj):
        return str(obj.pk)

    def validate_name(self, value):
        return validate_bilingual(value, "name")
