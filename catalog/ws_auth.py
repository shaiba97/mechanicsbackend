import logging

import jwt
from urllib.parse import parse_qs

from django.conf import settings

logger = logging.getLogger(__name__)


class JWTAuthMiddleware:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "websocket":
            scope["jwt_payload"] = self.decode_token(scope)
        return await self.inner(scope, receive, send)

    def decode_token(self, scope):
        params = parse_qs(scope.get("query_string", b"").decode("utf-8"))
        token = (params.get("token") or [None])[0]
        if not token:
            return None
        secret = getattr(settings, "JWT_SECRET", "")
        if not secret:
            return None
        try:
            return jwt.decode(token, secret, algorithms=["HS256"])
        except jwt.PyJWTError as exc:
            logger.info("WS JWT rejected: %s", type(exc).__name__)
            return None
