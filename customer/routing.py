from django.urls import path

from .consumers import CustomerOrdersConsumer

websocket_urlpatterns = [
    path(
        "ws/customer/orders/<uuid:order_id>/",
        CustomerOrdersConsumer.as_asgi(),
    ),
]
