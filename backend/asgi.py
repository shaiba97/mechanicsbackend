import os
import asyncio

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import OriginValidator
from django.conf import settings

from catalog.routing import websocket_urlpatterns as catalog_ws_urlpatterns
from catalog.ws_auth import JWTAuthMiddleware
from customer.routing import websocket_urlpatterns as customer_ws_urlpatterns

import store_ops.broadcast as broadcast_mod


class MainLoopCaptureMiddleware:
    """Capture daphne's main event loop for cross-loop channel broadcasts."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket" and broadcast_mod._MAIN_LOOP is None:
            broadcast_mod.set_main_loop(asyncio.get_running_loop())
        return await self.app(scope, receive, send)


def allowed_hosts_origin_validator(application):
    allowed_hosts = settings.ALLOWED_HOSTS
    if settings.DEBUG and not allowed_hosts:
        allowed_hosts = ['localhost', '127.0.0.1', '[::1]']
    validator = OriginValidator(application, allowed_hosts)

    async def validate(scope, receive, send):
        if scope['type'] == 'websocket':
            has_origin = any(
                name == b'origin' for name, _ in scope.get('headers', [])
            )
            if not has_origin:
                return await application(scope, receive, send)
        return await validator(scope, receive, send)

    return validate


application = ProtocolTypeRouter(
    {
        'http': django_asgi_app,
        'websocket': allowed_hosts_origin_validator(
            MainLoopCaptureMiddleware(
                JWTAuthMiddleware(
                    URLRouter(
                        [*catalog_ws_urlpatterns, *customer_ws_urlpatterns]
                    )
                )
            )
        ),
    }
)
