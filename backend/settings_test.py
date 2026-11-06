import os

from .settings import *  # noqa: F401,F403

# Tests run against an in-memory SQLite database so `manage.py test` never
# touches (or attempts to create) a database on the remote Postgres host.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Encrypted-at-rest fields (admin_api.fields.EncryptedCharField) need a
# Fernet key; urlsafe-base64 of 32 bytes - deterministic throwaway, tests only.
os.environ.setdefault(
    "ENCRYPTION_KEY",
    "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE=",
)

# Channels tests do not run here, but keep the ASGI stack importable.
ASGI_APPLICATION = "backend.asgi.application"
