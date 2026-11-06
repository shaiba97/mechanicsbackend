import base64

from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import KycDocument, StoreProfile, StoreSettings
from .serializers import (
    KycDocumentSerializer,
    KycDocumentUploadSerializer,
    StoreProfileSerializer,
    StoreSettingsSerializer,
)


def _require_store(request):
    store = getattr(request.user, "store", None)
    if store is None:
        raise PermissionDenied("Authenticated user is not linked to a store.")
    return store


class StoreProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store = _require_store(request)
        profile, _ = StoreProfile.objects.get_or_create(store=store)
        serializer = StoreProfileSerializer(profile, context={"view": self})
        return Response(serializer.data)

    def put(self, request):
        store = _require_store(request)
        profile, _ = StoreProfile.objects.get_or_create(store=store)
        serializer = StoreProfileSerializer(
            profile, data=request.data, partial=True, context={"view": self}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class StoreSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store = _require_store(request)
        settings, _ = StoreSettings.objects.get_or_create(store=store)
        serializer = StoreSettingsSerializer(settings, context={"view": self})
        return Response(serializer.data)

    def put(self, request):
        store = _require_store(request)
        settings, _ = StoreSettings.objects.get_or_create(store=store)
        serializer = StoreSettingsSerializer(
            settings, data=request.data, partial=True, context={"view": self}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class KycDocumentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        store = _require_store(request)
        docs = KycDocument.objects.filter(store=store).order_by("-uploaded_at")
        serializer = KycDocumentSerializer(docs, many=True)
        return Response(serializer.data)

    def post(self, request):
        store = _require_store(request)
        serializer = KycDocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        doc_type = serializer.validated_data["docType"]
        try:
            file_data = base64.b64decode(
                serializer.validated_data["fileData"], validate=True
            )
        except (ValueError, Exception):
            raise serializers.ValidationError(
                {"fileData": ["Invalid base64 file data."]}
            )
        if len(file_data) > 5 * 1024 * 1024:
            raise serializers.ValidationError(
                {"fileData": ["File too large (max 5MB)."]}
            )

        doc, _ = KycDocument.objects.update_or_create(
            store=store,
            doc_type=doc_type,
            defaults={
                "file_name": serializer.validated_data["fileName"],
                "content_type": serializer.validated_data["contentType"],
                "file_data": file_data,
                "status": KycDocument.DocStatus.UPLOADED,
                "verified_at": None,
            },
        )
        return Response(
            KycDocumentSerializer(doc).data, status=status.HTTP_201_CREATED
        )
