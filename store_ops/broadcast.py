import asyncio

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

# Captured from daphne's main event loop (see MainLoopCaptureMiddleware in
# backend/asgi.py). Broadcasts fire from sync API views running on worker
# threads; async_to_sync(channel_layer.group_send) from those threads silently
# drops messages against InMemoryChannelLayer (which lives on the main loop),
# so we route group_send through run_coroutine_threadsafe on the main loop.
_MAIN_LOOP = None


def set_main_loop(loop):
    global _MAIN_LOOP
    _MAIN_LOOP = loop


def _group_send(channel_layer, group, event):
    loop = _MAIN_LOOP
    if loop is not None and loop.is_running():
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # Sync worker thread: schedule on daphne's main loop so the
            # consumer's channel_receive (also on the main loop) picks it up.
            asyncio.run_coroutine_threadsafe(
                channel_layer.group_send(group, event), loop
            )
            return
    async_to_sync(channel_layer.group_send)(group, event)


def broadcast_order_new(store_id, order_payload):
    channel_layer = get_channel_layer()
    if channel_layer is None or store_id is None:
        return
    _group_send(
        channel_layer,
        f"store_{store_id}",
        {"type": "order_new", "order": order_payload},
    )


def broadcast_order_status(store_id, order_id, status_value):
    channel_layer = get_channel_layer()
    if channel_layer is None or store_id is None:
        return
    _group_send(
        channel_layer,
        f"store_{store_id}",
        {"type": "order_status", "orderId": str(order_id), "status": status_value},
    )


def broadcast_customer_order_status(order_id, status_value, handover_otp=None):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    event = {
        "type": "order_status",
        "orderId": str(order_id),
        "status": status_value,
    }
    if handover_otp:
        event["handoverOtp"] = handover_otp
    _group_send(channel_layer, f"order_{order_id}", event)
