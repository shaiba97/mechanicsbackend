from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('auth', include(('authentication.urls', 'authentication'), namespace='authentication')),
    path('auth/', include(('authentication.urls', 'authentication2'), namespace='authentication2')),
    path('api/', include('catalog.urls')),
    path('api/', include('inventory.urls')),
    path('api/store/', include('store_profile.urls')),
    path('api/store/', include('store_ops.urls')),
    path('api/', include('admin_api.urls')),
    path('api/', include('customer.urls')),
]
