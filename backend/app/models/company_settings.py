"""Per-company settings document.

The Settings page is one flat key/value form spanning a dozen sections
(organisation, preferences, GST/TDS rules, tolerances, notifications, ...).
Rather than invent a column per field and churn the schema every time the UI
grows a toggle, PAL keeps the whole form as one JSON document per company. One
row per company: `company_id` is the primary key.

Users, roles and secrets are deliberately NOT stored here. They belong to an
authentication layer that does not exist yet; this table holds only the
non-secret configuration the Settings form collects.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CompanySettings(Base):
    """The Settings form for one company, stored as a single JSON document."""

    __tablename__ = "company_settings"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<CompanySettings company_id={self.company_id} keys={len(self.data)}>"


__all__ = ["CompanySettings"]
