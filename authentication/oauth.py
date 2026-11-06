import json
import secrets
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core import signing


class OAuthError(Exception):
    pass


STATE_SALT = "auth.oauth.state"
STATE_MAX_AGE = 600

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_SCOPE = "openid email profile"

FACEBOOK_AUTHORIZE_URL = "https://www.facebook.com/v21.0/dialog/oauth"
FACEBOOK_TOKEN_URL = "https://graph.facebook.com/v21.0/oauth/access_token"
FACEBOOK_USERINFO_URL = "https://graph.facebook.com/v21.0/me"
FACEBOOK_USERINFO_FIELDS = "id,name,email,picture.type(large)"
FACEBOOK_SCOPE = "email,public_profile"


def provider_config(provider: str) -> dict | None:
    config = getattr(settings, "SOCIAL_OAUTH", {}).get(provider)
    if not config:
        return None
    if not config.get("client_id") or not config.get("client_secret"):
        return None
    return config


def provider_configured(provider: str) -> bool:
    config = getattr(settings, "SOCIAL_OAUTH", {}).get(provider)
    return bool(config and config.get("client_id") and config.get("client_secret"))


def store_redirect_url() -> str:
    return getattr(settings, "STORE_REDIRECT_URL", "http://localhost:3100")


def callback_success_url(refresh_token: str) -> str:
    return store_redirect_url() + "/auth/callback?" + urllib.parse.urlencode(
        {"refresh_token": refresh_token}
    )


def callback_error_url(reason: str) -> str:
    return store_redirect_url() + "/auth/callback?" + urllib.parse.urlencode({"error": reason})


def build_authorize_url(provider: str, redirect_uri: str) -> str:
    config = provider_config(provider)
    if config is None:
        raise OAuthError("not_configured")
    state = signing.dumps({"p": provider, "n": secrets.token_urlsafe(16)}, salt=STATE_SALT)
    if provider == "google":
        base = GOOGLE_AUTHORIZE_URL
    elif provider == "facebook":
        base = FACEBOOK_AUTHORIZE_URL
    else:
        raise OAuthError("unknown_provider")
    query = urllib.parse.urlencode(
        {
            "client_id": config["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "scope": GOOGLE_SCOPE if provider == "google" else FACEBOOK_SCOPE,
            "access_type": "offline" if provider == "google" else "",
        }
    )
    return f"{base}?{query}"


def verify_state(raw_state: str, provider: str) -> None:
    try:
        data = signing.loads(raw_state, salt=STATE_SALT, max_age=STATE_MAX_AGE)
    except signing.BadSignature as exc:
        raise OAuthError("bad_state") from exc
    if data.get("p") != provider:
        raise OAuthError("bad_state")


def _http_json(url: str, data: dict | None = None, headers: dict | None = None) -> dict:
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        raise OAuthError("exchange_failed") from exc


def exchange_code(provider: str, code: str, redirect_uri: str) -> dict:
    config = provider_config(provider)
    if config is None:
        raise OAuthError("not_configured")
    if provider == "google":
        token_payload = _http_json(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "redirect_uri": redirect_uri,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        access_token = token_payload.get("access_token")
        if not access_token:
            raise OAuthError("exchange_failed")
        profile_raw = _http_json(
            GOOGLE_USERINFO_URL, headers={"accept": "application/json", "authorization": f"Bearer {access_token}"}
        )
    elif provider == "facebook":
        token_payload = _http_json(
            FACEBOOK_TOKEN_URL,
            data={
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        access_token = token_payload.get("access_token")
        if not access_token:
            raise OAuthError("exchange_failed")
        query = urllib.parse.urlencode({"fields": FACEBOOK_USERINFO_FIELDS, "access_token": access_token})
        profile_raw = _http_json(f"{FACEBOOK_USERINFO_URL}?{query}")
    else:
        raise OAuthError("unknown_provider")

    email = profile_raw.get("email")
    if not email:
        raise OAuthError("email_missing")
    if provider == "google":
        provider_id = str(profile_raw.get("sub") or "")
    else:
        provider_id = str(profile_raw.get("id") or "")
    picture = profile_raw.get("picture")
    if isinstance(picture, dict):
        picture = picture.get("data", {}).get("url")
    profile = {
        "id": provider_id,
        "email": email.lower(),
        "name": profile_raw.get("name") or email,
        "picture": picture or None,
    }
    return profile


def oauth_user(profile: dict, provider: str):
    from django.contrib.auth.hashers import make_password
    from .models import User

    user = User.objects.filter(email=profile["email"]).first()
    if user is None:
        user = User.objects.create(
            email=profile["email"],
            full_name=(profile["name"] or profile["email"])[:120],
            auth_provider=provider,
            provider_id=profile["id"] or None,
            avatar_url=profile["picture"],
            password=make_password(secrets.token_urlsafe(32)),
        )
        return user, True
    if user.provider_id != profile["id"] or (profile["picture"] and user.avatar_url != profile["picture"]):
        user.provider_id = user.provider_id or profile["id"] or None
        user.avatar_url = profile["picture"] or user.avatar_url
        user.save(update_fields=["provider_id", "avatar_url", "updated_at"])
    return user, False
