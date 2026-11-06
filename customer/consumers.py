import json

from asgiref.sync import async_to_sync
from channels.generic.websocket import WebsocketConsumer


class CustomerOrdersConsumer(WebsocketConsumer):
    """Per-order WebSocket for customers: live handover and delivery status.

    Auth: the JWT access token (validated by JWTAuthMiddleware) identifies the
    customer via `sub`. The order group name is an unguessable UUID, so another
    customer cannot subscribe to an order they do not know about. Matches the
    trust model of catalog/OrdersConsumer (storeId claim, no DB round-trip in
    connect() — keeping connect() DB-free also keeps WebsocketCommunicator
    tests deterministic against SQLite).
    """

    def connect(self):
        payload = self.scope.get("jwt_payload")
        if not payload or payload.get("type") != "access":
            self.close(code=4401)
            return

        self.user_id = str(payload.get("sub") or "")
        if not self.user_id:
            self.close(code=4403)
            return

        self.order_id = str(self.scope["url_route"]["kwargs"]["order_id"])
        self.group_name = f"order_{self.order_id}"

        async_to_sync(self.channel_layer.group_add)(
            self.group_name, self.channel_name
        )
        self.accept()
        self.send(
            text_data=json.dumps(
                {"type": "order:connected", "orderId": self.order_id}
            )
        )

    def disconnect(self, code):
        if hasattr(self, "group_name"):
            async_to_sync(self.channel_layer.group_discard)(
                self.group_name, self.channel_name
            )

    def order_status(self, event):
        self.send(
            text_data=json.dumps(
                {
                    "type": "order:status",
                    "orderId": event["orderId"],
                    "status": event["status"],
                    "handoverOtp": event.get("handoverOtp") or "",
                }
            )
        )

    def receive(self, text_data=None, bytes_data=None):
        try:
            content = json.loads(text_data or "{}")
        except ValueError:
            return
        if content.get("type") == "ping":
            self.send(text_data=json.dumps({"type": "pong"}))
