from django.urls import path

from .consumers import CatalogConsumer, OrdersConsumer

websocket_urlpatterns = [
    path("ws/catalog/", CatalogConsumer.as_asgi()),
    path("ws/orders/", OrdersConsumer.as_asgi()),
]
