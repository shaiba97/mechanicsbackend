import json
import logging

from django.contrib.auth import authenticate
from django.contrib.auth.hashers import make_password
from django.http import HttpResponseRedirect
from django.urls import reverse
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import oauth
from .models import AuthStore, User
from .serializers import AuthSerializer, ChangePasswordSerializer, MeUpdateSerializer, RefreshSerializer
from .throttling import allow_burst, client_ip
from .tokens import (
    auth_response,
    bearer_user,
    revoke_raw_token,
    revoke_family,
    rotate_refresh_token,
    TokenError,
    user_payload,
)

logger = logging.getLogger(__name__)

REGISTER_RATE = 5
LOGIN_RATE = 10


def json_error(status_code: int, message: str) -> Response:
    return Response({"message": message}, status=status_code)


@api_view(["POST"])
def auth(request):
    """
    Unified auth endpoint: 
    - If phone number exists → login
    - If phone number doesn't exist → sign up (auto-create account)
    - Returns auth tokens in both cases
    """
    if not allow_burst(f"auth:{client_ip(request)}", max(REGISTER_RATE, LOGIN_RATE)):
        return json_error(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts, try again soon.")

    serializer = AuthSerializer(data=request.data)
    if not serializer.is_valid():
        message = "; ".join(
            " ".join(errors) if isinstance(errors, list) else str(errors)
            for errors in serializer.errors.values()
        )
        return json_error(status.HTTP_400_BAD_REQUEST, message)

    data = serializer.validated_data
    mode = data.get("mode", "login")
    email = data.get("email", "").strip()

    if mode == "signup":
        # --- SIGN UP flow ---
        if User.objects.filter(email=email).exists():
            return json_error(status.HTTP_409_CONFLICT, "This email is already registered.")

        store_name = data.get("storeName", "").strip()
        user = User.objects.create(
            email=email,
            full_name=data.get("fullName", "").strip(),
            auth_provider=User.AuthProvider.LOCAL,
            role=User.Role.STORE_ADMIN if store_name else User.Role.CUSTOMER,
        )
        if store_name:
            user.store = AuthStore.objects.create(
                name=store_name, cr_number=data.get("crNumber") or None
            )
            user.save(update_fields=["store"])
        logger.info("User signed up: %s", email)
        tokens = auth_response(user)
        return Response({**tokens, "phoneNumber": email, "mode": "signup"}, status=status.HTTP_201_CREATED)

    elif mode == "login":
        # --- LOGIN flow ---
        user = data["user"]
        if not user.is_active:
            return json_error(status.HTTP_401_UNAUTHORIZED, "This account is disabled.")
        logger.info("User logged in: %s", user.email)
        tokens = auth_response(user)
        return Response({**tokens, "phoneNumber": user.email, "mode": "login"})


@api_view(["POST"])
def admin_login(request):
    """
    Admin panel login: email + password with real password verification
    and an is_staff/is_superuser gate. Unlike /auth/login, accounts cannot
    be created here and no passwordless access is possible.
    """
    if not allow_burst(f"adminlogin:{client_ip(request)}", LOGIN_RATE):
        return json_error(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts, try again soon.")

    if not isinstance(request.data, dict):
        return json_error(status.HTTP_400_BAD_REQUEST, "Email and password are required.")
    email = str(request.data.get("email") or "").strip().lower()
    password = str(request.data.get("password") or "")
    if not email or not password:
        return json_error(status.HTTP_400_BAD_REQUEST, "Email and password are required.")

    user = User.objects.filter(email__iexact=email).first()
    if user is None or not user.is_active or not user.check_password(password):
        return json_error(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password.")
    if not (user.is_staff or user.is_superuser):
        return json_error(status.HTTP_403_FORBIDDEN, "This account does not have admin access.")

    logger.info("Admin logged in: %s", user.email)
    return Response({**auth_response(user), "mode": "admin"})


@api_view(["POST"])
def refresh(request):
    serializer = RefreshSerializer(data=request.data)
    if not serializer.is_valid():
        return json_error(status.HTTP_401_UNAUTHORIZED, "Missing or malformed refresh token.")
    try:
        return Response(rotate_refresh_token(serializer.validated_data["refreshToken"]))
    except TokenError as exc:
        if exc.code == "reuse":
            logger.warning("Refresh token reuse detected (family revoked).")
            return json_error(status.HTTP_401_UNAUTHORIZED, "Session revoked, please sign in again.")
        return json_error(status.HTTP_401_UNAUTHORIZED, "Session expired, please sign in again.")


@api_view(["POST"])
def logout(request):
    raw = ""
    if isinstance(request.data, dict):
        raw = str(request.data.get("refreshToken") or "")
    if raw:
        revoke_raw_token(raw)
    return Response({"ok": True})


@api_view(["GET", "PATCH"])
def me(request):
    user = bearer_user(request)
    if user is None:
        return json_error(status.HTTP_401_UNAUTHORIZED, "Session expired, please sign in again.")
    if request.method == "PATCH":
        serializer = MeUpdateSerializer(data=request.data, context={"user": user})
        if not serializer.is_valid():
            message = "; ".join(
                " ".join(errors) if isinstance(errors, list) else str(errors)
                for errors in serializer.errors.values()
            )
            return json_error(status.HTTP_400_BAD_REQUEST, message)
        data = serializer.validated_data
        uses_phone = user.email.isdigit()
        updates = []
        if "fullName" in data:
            user.full_name = data["fullName"]
            updates.append("full_name")
        if "city" in data:
            user.city = data["city"]
            updates.append("city")
        if "street" in data:
            user.street = data["street"]
            updates.append("street")
        identifier = data.get("phone") if uses_phone else data.get("email")
        if identifier:
            identifier = identifier.strip().lower()
            if User.objects.filter(email=identifier).exclude(pk=user.pk).exists():
                return json_error(status.HTTP_409_CONFLICT, "This identifier is already used by another account.")
            field = "email"
            setattr(user, field, identifier)
            updates.append(field)
        if updates:
            user.save(update_fields=updates)
        return Response({"user": user_payload(user)})
    return Response({"user": user_payload(user)})


@api_view(["POST"])
def change_password(request):
    user = bearer_user(request)
    if user is None:
        return json_error(status.HTTP_401_UNAUTHORIZED, "Session expired, please sign in again.")

    serializer = ChangePasswordSerializer(data=request.data, context={"user": user})
    if not serializer.is_valid():
        message = "; ".join(
            " ".join(errors) if isinstance(errors, list) else str(errors)
            for errors in serializer.errors.values()
        )
        return json_error(status.HTTP_400_BAD_REQUEST, message)

    new_password = serializer.validated_data["newPassword"]
    user.set_password(new_password)
    user.save(update_fields=["password"])

    # Revoke all other refresh tokens so the user must re-sign in elsewhere
    RefreshToken.objects.filter(user=user, revoked_at__isnull=True).update(
        revoked_at=timezone.now()
    )

    return Response({"message": "Password updated successfully"})


@api_view(["GET"])
def providers(request):
    return Response(
        {
            "google": oauth.provider_configured("google"),
            "facebook": oauth.provider_configured("facebook"),
        }
    )


def _provider_name(provider: str) -> str | None:
    return provider if provider in (User.AuthProvider.GOOGLE, User.AuthProvider.FACEBOOK) else None


@api_view(["GET"])
def oauth_redirect(request, provider):
    provider = _provider_name(provider)
    if not provider:
        return json_error(status.HTTP_404_NOT_FOUND, "Unknown provider.")
    if not oauth.provider_configured(provider):
        return HttpResponseRedirect(oauth.callback_error_url("not_configured"))
    callback_path = reverse("authentication:oauth-callback", args=[provider])
    redirect_uri = request.build_absolute_uri(callback_path)
    try:
        authorize_url = oauth.build_authorize_url(provider, redirect_uri)
    except oauth.OAuthError:
        return HttpResponseRedirect(oauth.callback_error_url("not_configured"))
    return HttpResponseRedirect(authorize_url)


@api_view(["GET"])
def oauth_callback(request, provider):
    provider = _provider_name(provider)
    if not provider:
        return json_error(status.HTTP_404_NOT_FOUND, "Unknown provider.")
    raw_state = request.query_params.get("state", "")
    code = request.query_params.get("code")
    try:
        oauth.verify_state(raw_state, provider)
    except oauth.OAuthError:
        return HttpResponseRedirect(oauth.callback_error_url("bad_state"))
    if not code:
        return HttpResponseRedirect(oauth.callback_error_url("provider_denied"))
    callback_path = reverse("authentication:oauth-callback", args=[provider])
    redirect_uri = request.build_absolute_uri(callback_path)
    try:
        profile = oauth.exchange_code(provider, code, redirect_uri)
        user, _created = oauth.oauth_user(profile, provider)
    except oauth.OAuthError as exc:
        logger.info("OAuth callback failed for %s: %s", provider, exc.code)
        return HttpResponseRedirect(oauth.callback_error_url(exc.code))
    if not user.is_active:
        return HttpResponseRedirect(oauth.callback_error_url("account_disabled"))
    tokens = auth_response(user)
    return HttpResponseRedirect(oauth.callback_success_url(tokens["refreshToken"]))