"""Encrypted secrets saved from the Settings page, such as the DeepSeek key.

Install-wide, one row per secret name. The value is Fernet ciphertext; see
`app.core.secrets`. When tenants arrive this moves onto the tenant record.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AppSecret(Base):
    __tablename__ = "app_secrets"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        # Never include the ciphertext.
        return f"<AppSecret name={self.name}>"


__all__ = ["AppSecret"]
