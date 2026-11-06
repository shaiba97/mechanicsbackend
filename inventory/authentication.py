import jwt
from django.conf import settings
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header

from authentication.models import User


class JWTAuthentication(BaseAuthentication):
    def authenticate(self, request):
        header = get_authorization_header(request).decode('utf-8')
        if not header.lower().startswith('bearer '):
            return None
        token = header.split(' ', 1)[1].strip()
        try:
            payload = jwt.decode(token, settings.JWT_SECRET, algorithms=['HS256'])
        except jwt.PyJWTError:
            raise exceptions.AuthenticationFailed('Invalid or expired token.')
        if payload.get('type') != 'access':
            raise exceptions.AuthenticationFailed('Invalid token type.')
        try:
            user = User.objects.get(pk=payload.get('sub'))
        except (User.DoesNotExist, ValueError):
            raise exceptions.AuthenticationFailed('User not found.')
        if not user.is_active:
            raise exceptions.AuthenticationFailed('User is inactive.')
        return (user, token)

    def authenticate_header(self, request):
        return 'Bearer realm="api"'
