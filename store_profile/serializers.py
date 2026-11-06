import base64

from rest_framework import serializers

from .models import KycDocument, StoreProfile, StoreSettings


class StoreProfileSerializer(serializers.ModelSerializer):
    id = serializers.SerializerMethodField()
    storeId = serializers.SerializerMethodField()
    ownerFullName = serializers.CharField(
        source="owner_full_name", required=False, allow_blank=True
    )
    nationalId = serializers.CharField(
        source="national_id", required=False, allow_blank=True
    )
    mobilePhone = serializers.CharField(
        source="mobile_phone", required=False, allow_blank=True
    )
    contactEmail = serializers.EmailField(
        source="contact_email", required=False, allow_blank=True
    )
    commercialNameAr = serializers.CharField(
        source="commercial_name_ar", required=False, allow_blank=True
    )
    commercialNameEn = serializers.CharField(
        source="commercial_name_en", required=False, allow_blank=True
    )
    crNumber = serializers.CharField(
        source="cr_number", required=False, allow_blank=True
    )
    vatNumber = serializers.CharField(
        source="vat_number", required=False, allow_blank=True
    )
    addressStreet = serializers.CharField(
        source="address_street", required=False, allow_blank=True
    )
    buildingNumber = serializers.CharField(
        source="building_number", required=False, allow_blank=True
    )
    postalCode = serializers.CharField(
        source="postal_code", required=False, allow_blank=True
    )
    isVerified = serializers.BooleanField(source="is_verified", read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = StoreProfile
        fields = [
            "id",
            "storeId",
            "ownerFullName",
            "nationalId",
            "mobilePhone",
            "contactEmail",
            "commercialNameAr",
            "commercialNameEn",
            "crNumber",
            "vatNumber",
            "city",
            "region",
            "addressStreet",
            "buildingNumber",
            "postalCode",
            "status",
            "isVerified",
            "createdAt",
            "updatedAt",
        ]
        read_only_fields = ("status", "is_verified")

    def get_id(self, obj):
        return str(obj.pk)

    def get_storeId(self, obj):
        return str(obj.store_id) if obj.store_id else None

    def validate_crNumber(self, value):
        cleaned = (value or "").strip()
        if cleaned and not (cleaned.isdigit() and len(cleaned) == 10):
            raise serializers.ValidationError("CR number must be 10 digits.")
        return cleaned

    def validate_vatNumber(self, value):
        cleaned = (value or "").strip()
        if cleaned and not (cleaned.isdigit() and len(cleaned) == 15):
            raise serializers.ValidationError("VAT number must be 15 digits.")
        return cleaned

    def validate_mobilePhone(self, value):
        cleaned = (value or "").strip()
        if cleaned and not cleaned.startswith("+966"):
            raise serializers.ValidationError("Mobile phone must start with +966.")
        return cleaned


class StoreSettingsSerializer(serializers.ModelSerializer):
    id = serializers.SerializerMethodField()
    storeId = serializers.SerializerMethodField()
    bankName = serializers.CharField(
        source="bank_name", required=False, allow_blank=True
    )
    accountHolderName = serializers.CharField(
        source="account_holder_name", required=False, allow_blank=True
    )
    stripeConnected = serializers.BooleanField(
        source="stripe_connected", read_only=True
    )
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = StoreSettings
        fields = [
            "id",
            "storeId",
            "bankName",
            "accountHolderName",
            "iban",
            "stripeConnected",
            "currency",
            "updatedAt",
        ]
        read_only_fields = ("stripeConnected", "currency")

    def get_id(self, obj):
        return str(obj.pk)

    def get_storeId(self, obj):
        return str(obj.store_id) if obj.store_id else None

    def validate_iban(self, value):
        cleaned = (value or "").strip()
        if cleaned and not (
            cleaned.startswith("SA") and len(cleaned) == 24 and cleaned[2:].isdigit()
        ):
            raise serializers.ValidationError(
                "Saudi IBAN must start with SA followed by 22 digits."
            )
        return cleaned


class KycDocumentSerializer(serializers.ModelSerializer):
    id = serializers.SerializerMethodField()
    storeId = serializers.SerializerMethodField()
    docType = serializers.CharField(source="doc_type", read_only=True)
    fileName = serializers.CharField(source="file_name", read_only=True)
    contentType = serializers.CharField(source="content_type", read_only=True)
    status = serializers.CharField(read_only=True)
    uploadedAt = serializers.DateTimeField(source="uploaded_at", read_only=True)
    verifiedAt = serializers.DateTimeField(source="verified_at", read_only=True)

    class Meta:
        model = KycDocument
        fields = [
            "id",
            "storeId",
            "docType",
            "fileName",
            "contentType",
            "status",
            "uploadedAt",
            "verifiedAt",
        ]

    def get_id(self, obj):
        return str(obj.pk)

    def get_storeId(self, obj):
        return str(obj.store_id) if obj.store_id else None


class KycDocumentUploadSerializer(serializers.Serializer):
    docType = serializers.ChoiceField(
        choices=[c[0] for c in KycDocument.DocType.choices]
    )
    fileName = serializers.CharField(max_length=255)
    contentType = serializers.CharField(max_length=100)
    fileData = serializers.CharField()

    def validate_fileData(self, value):
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, Exception):
            raise serializers.ValidationError("Invalid base64 file data.")
        if len(raw) > 5 * 1024 * 1024:
            raise serializers.ValidationError("File too large (max 5MB).")
        return value
