from rest_framework import serializers

from .models import PlatformCommissionRate


class PlatformCommissionRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlatformCommissionRate
        fields = ["id", "rate", "updated_at", "updated_by"]
        read_only_fields = ["id", "updated_at", "updated_by"]


class CommissionRateUpdateSerializer(serializers.Serializer):
    rate = serializers.DecimalField(
        max_digits=7, decimal_places=4, min_value=0, max_value=100
    )

    def validate_rate(self, value):
        if value is None or value < 0 or value > 100:
            raise serializers.ValidationError("Rate must be between 0 and 100 percent.")
        return value
