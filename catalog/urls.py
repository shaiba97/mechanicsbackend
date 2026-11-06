from rest_framework.routers import DefaultRouter

from .views import BrandViewSet, CarViewSet, CategoryViewSet

router = DefaultRouter()
router.register("brands", BrandViewSet)
router.register("cars", CarViewSet)
router.register("categories", CategoryViewSet)

urlpatterns = router.urls
