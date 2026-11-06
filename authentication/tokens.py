import hashlib
import secrets
import uuid
from datetime import timedelta

import jwt
from django.conf import settings
from django.utils import timezone

from .models import RefreshToken, User


class TokenError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _now():
    return timezone.now()


def issue_access_token(user: User) -> str:
    now = _now()
    ttl = getattr(settings, "JWT_ACCESS_TTL_SECONDS", 900)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "fullName": user.full_name,
        "role": user.role,
        "storeId": str(user.store_id) if user.store_id else None,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def decode_access_token(token: str) -> dict | None:
    secret = getattr(settings, "JWT_SECRET", "")
    if not secret:
        return None
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "access":
        return None
    return payload


def user_payload(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "fullName": user.full_name,
        "role": user.role,
        "storeId": str(user.store_id) if user.store_id else None,
        "authProvider": user.auth_provider,
        "avatarUrl": user.avatar_url,
        "isActive": user.is_active,
        "city": user.city,
        "street": user.street,
        "createdAt": user.created_at.isoformat().replace("+00:00", "Z"),
    }


def _hash_raw(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_refresh_token(user: User, family_id: uuid.UUID | None = None) -> str:
    raw = secrets.token_urlsafe(48)
    ttl = getattr(settings, "JWT_REFRESH_TTL_SECONDS", 604800)
    RefreshToken.objects.create(
        user=user,
        token_hash=_hash_raw(raw),
        family_id=family_id or uuid.uuid4(),
        expires_at=_now() + timedelta(seconds=ttl),
    )
    return raw


def auth_response(user: User, family_id: uuid.UUID | None = None) -> dict:
    return {
        "user": user_payload(user),
        "accessToken": issue_access_token(user),
        "refreshToken": issue_refresh_token(user, family_id),
    }


def bearer_user(request) -> User | None:
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        return None
    payload = decode_access_token(header.removeprefix("Bearer ").strip())
    if not payload:
        return None
    try:
        user = User.objects.get(pk=payload["sub"])
    except (User.DoesNotExist, ValueError, KeyError):
        return None
    if not user.is_active:
        return None
    return user


def rotate_refresh_token(raw: str) -> dict:
    row = RefreshToken.objects.select_related("user").filter(token_hash=_hash_raw(raw)).first()
    if row is None:
        raise TokenError("invalid")
    user = row.user
    if row.revoked_at is not None:
        revoke_family(row.family_id)
        raise TokenError("reuse")
    if row.is_expired:
        raise TokenError("expired")
    if not user.is_active:
        raise TokenError("invalid")

    swapped = RefreshToken.objects.filter(pk=row.pk, revoked_at__isnull=True).update(
        revoked_at=_now()
    )
    if swapped == 0:
        revoke_family(row.family_id)
        raise TokenError("reuse")

    return {
        "user": user_payload(user),
        "accessToken": issue_access_token(user),
        "refreshToken": issue_refresh_token(user, row.family_id),
    }


def revoke_family(family_id: uuid.UUID) -> None:
    RefreshToken.objects.filter(family_id=family_id, revoked_at__isnull=True).update(
        revoked_at=_now()
    )


def revoke_raw_token(raw: str) -> None:
    RefreshToken.objects.filter(token_hash=_hash_raw(raw), revoked_at__isnull=True).update(
        revoked_at=_now()
    )
