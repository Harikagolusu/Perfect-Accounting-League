"""The DeepSeek API key: saved from Settings, else the DEEPSEEK_API_KEY env var.

Read fresh on every provider construction so a key saved through one worker
process is used by all of them without a restart. The plaintext is never
returned to the browser; callers get `masked()` instead.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.secrets import decrypt, encrypt
from app.models.app_secret import AppSecret

logger = logging.getLogger(__name__)

DEEPSEEK_KEY = "deepseek_api_key"

#: Swapped for the test database's factory by the test suite.
session_factory: sessionmaker = SessionLocal


def stored_key(db: Session) -> str | None:
    row = db.get(AppSecret, DEEPSEEK_KEY)
    return decrypt(row.ciphertext) if row is not None else None


def current_key() -> tuple[str, str | None]:
    """(key, source) where source is "settings", "environment" or None."""
    try:
        with session_factory() as db:
            saved = stored_key(db)
    except Exception:  # noqa: BLE001 - an unreachable DB falls back to the env key
        logger.warning("Could not read the saved AI key; using the environment key.")
        saved = None
    if saved:
        return saved, "settings"
    env = get_settings().deepseek_api_key.strip()
    return (env, "environment") if env else ("", None)


def save_key(db: Session, key: str) -> None:
    row = db.get(AppSecret, DEEPSEEK_KEY)
    if row is None:
        db.add(AppSecret(name=DEEPSEEK_KEY, ciphertext=encrypt(key)))
    else:
        row.ciphertext = encrypt(key)
    db.commit()


def delete_key(db: Session) -> None:
    row = db.get(AppSecret, DEEPSEEK_KEY)
    if row is not None:
        db.delete(row)
        db.commit()


def masked(key: str) -> str | None:
    """Enough to recognise the key, never enough to use it."""
    if not key:
        return None
    return f"{key[:3]}…{key[-4:]}" if len(key) > 10 else "…"


__all__ = ["current_key", "delete_key", "masked", "save_key", "stored_key"]
