"""Encrypted model fields using cryptography.fernet.

The encryption key is read from the ENCRYPTION_KEY environment variable.
It must be a URL-safe base64-encoded 32-byte key. Generate one with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

import os

from cryptography.fernet import Fernet
from django.db import models


def _get_fernet() -> Fernet:
    key = os.environ.get("ENCRYPTION_KEY", "")
    if not key:
        raise ValueError(
            "ENCRYPTION_KEY environment variable is not set. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode())


class EncryptedCharField(models.CharField):
    """A CharField that stores values encrypted at rest using Fernet symmetric encryption."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", 512)
        super().__init__(*args, **kwargs)

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        try:
            return _get_fernet().decrypt(value.encode()).decode()
        except Exception:
            # If decryption fails (e.g. key rotated), return the raw value so
            # the admin can see something is stored and re-enter the secret.
            return value

    def get_prep_value(self, value):
        if value is None:
            return value
        return _get_fernet().encrypt(value.encode()).decode()

    def value_to_string(self, obj):
        value = self.value_from_object(obj)
        return self.get_prep_value(value)
