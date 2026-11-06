from channels.generic.websocket import AsyncJsonWebsocketConsumer


class CatalogConsumer(AsyncJsonWebsocketConsumer):
    GROUP = "catalog"

    async def connect(self):
        await self.channel_layer.group_add(self.GROUP, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.GROUP, self.channel_name)

    async def catalog_event(self, event):
        await self.send_json(
            {
                "type": "catalog.event",
                "entity": event["entity"],
                "action": event["action"],
                "data": event["data"],
            }
        )


class OrdersConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        await self.accept()
        payload = self.scope.get("jwt_payload")
        if not payload or payload.get("type") != "access":
            await self.close(code=4401)
            return
        store_id = payload.get("storeId")
        if not store_id:
            await self.close(code=4403)
            return
        self.group_name = f"store_{store_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.send_json({"type": "order:connected", "storeId": store_id})

    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content):
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    async def order_new(self, event):
        await self.send_json({"type": "order:new", "order": event["order"]})

    async def order_status(self, event):
        await self.send_json(
            {
                "type": "order:status",
                "orderId": event["orderId"],
                "status": event["status"],
            }
        )

    async def inventory_update(self, event):
        await self.send_json(
            {"type": "inventory:update", "partId": event["partId"], "qty": event["qty"]}
        )
