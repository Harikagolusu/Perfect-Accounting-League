"""Tally→PAL master data synchronisation log.

One row per sync run per company. Append-only: no row is ever updated or
deleted. Re-syncing always inserts a new row so the full sync history is
preserved.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TallySyncLog(Base):
    """One Tally→PAL ledger sync run for one company."""

    __tablename__ = "tally_sync_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    tally_company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ledgers_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ledgers_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ledgers_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ledgers_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[str] = mapped_column(
        String(255), nullable=False, default="local-user", server_default="local-user"
    )

    def __repr__(self) -> str:
        return (
            f"<TallySyncLog id={self.id} company_id={self.company_id} "
            f"tally={self.tally_company_name!r} created={self.ledgers_created} "
            f"updated={self.ledgers_updated}>"
        )


__all__ = ["TallySyncLog"]
