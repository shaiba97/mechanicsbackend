from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import InventoryItemViewSet, inventory_image_upload, inventory_image_serve

router = DefaultRouter()
router.register('inventory', InventoryItemViewSet, basename='inventory')

urlpatterns = router.urls + [
    path('inventory/images', inventory_image_upload),
    path('inventory/images/<uuid:image_id>', inventory_image_serve),
]
