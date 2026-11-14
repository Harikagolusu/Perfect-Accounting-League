"""Symmetric encryption for secrets saved from the UI.

The Fernet key is derived from `PAL_SECRET_KEY`. Outside production a missing
value falls back to a generated key file next to the storage folder, so a
local install works without extra setup. Production refuses to run without
the variable: a key file on an ephemeral host would vanish on redeploy and
silently lock every saved secret.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class SecretKeyMissing(RuntimeError):
    """Production has no PAL_SECRET_KEY configured."""


def _fernet() -> Fernet:
    settings = get_settings()
    secret = settings.pal_secret_key.strip()
    if not secret:
        if settings.is_production:
            raise SecretKeyMissing("PAL_SECRET_KEY is not set.")
        path = settings.document_storage_dir.parent / ".pal_secret_key"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(Fernet.generate_key().decode(), encoding="utf-8")
        secret = path.read_text(encoding="utf-8").strip()
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str | None:
    """The plaintext, or None when the secret key changed since it was saved."""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken:
        return None


__all__ = ["SecretKeyMissing", "decrypt", "encrypt"]
