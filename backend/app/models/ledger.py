"""Company-scoped Ledger Master.

The client's Phase 1 process starts with a ledger master before draft vouchers
can be prepared. A ledger is deliberately company-owned: the same ledger name
may exist in two companies' charts of accounts, and neither company may use the
other's record.

`ledger_type` and `parent_group` are free text rather than an invented enum.
The client has not supplied a fixed chart-of-accounts taxonomy, so PAL stores
the company's own grouping instead of guessing accounting categories.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LedgerSource(str, enum.Enum):
    """Where the ledger master record came from.

    Only `MANUAL` is created by the Phase 1 UI. The other values reserve the
    provenance field for the later Tally/Zoho import adapters.
    """

    MANUAL = "MANUAL"
    IMPORT = "IMPORT"
    TALLY = "TALLY"
    ZOHO = "ZOHO"


LEDGER_SOURCE_ENUM = SAEnum(
    LedgerSource,
    name="ledger_source",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class Ledger(Base):
    """A ledger in one company's chart of accounts."""

    __tablename__ = "ledgers"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_ledgers_company_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str | None] = mapped_column(String(64))
    #: Company-supplied grouping/category. Not constrained to a PAL-invented list.
    ledger_type: Mapped[str | None] = mapped_column(String(64))
    #: Tally-style parent group, when the company supplies one.
    parent_group: Mapped[str | None] = mapped_column(String(255))
    #: Optional explicit tax classification, e.g. CGST/SGST/IGST/Cess.
    tax_classification: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[LedgerSource] = mapped_column(
        LEDGER_SOURCE_ENUM,
        nullable=False,
        default=LedgerSource.MANUAL,
        server_default=LedgerSource.MANUAL.value,
    )
    external_tally_id: Mapped[str | None] = mapped_column(String(255))
    external_zoho_id: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
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
        return (
            f"<Ledger id={self.id} company_id={self.company_id} "
            f"name={self.name!r} active={self.is_active}>"
        )


__all__ = ["Ledger", "LedgerSource", "LEDGER_SOURCE_ENUM"]
