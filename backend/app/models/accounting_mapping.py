"""Traceable accounting mappings learned from approved postings or rules.

The client requires ledger suggestions to be explainable. This model records the
evidence behind a mapping instead of storing an opaque confidence score:

* the source type and optional vendor/counterparty/narration context;
* the ledger the company approved;
* who approved it and why.

A mapping is company-owned and never crosses company boundaries.
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
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AccountingMappingSourceType(str, enum.Enum):
    """The kind of source document a mapping applies to."""

    INVOICE = "INVOICE"
    BANK_TRANSACTION = "BANK_TRANSACTION"


class AccountingMappingMatchType(str, enum.Enum):
    """How a mapping is matched.

    `VENDOR` and `COUNTERPARTY` are identity matches. `NARRATION` is an approved
    text rule. `RULE` is an explicitly configured company rule. No fuzzy or
    AI-generated mapping is stored here.
    """

    VENDOR = "VENDOR"
    COUNTERPARTY = "COUNTERPARTY"
    NARRATION = "NARRATION"
    RULE = "RULE"


MAPPING_SOURCE_TYPE_ENUM = SAEnum(
    AccountingMappingSourceType,
    name="accounting_mapping_source_type",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)
MAPPING_MATCH_TYPE_ENUM = SAEnum(
    AccountingMappingMatchType,
    name="accounting_mapping_match_type",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class AccountingMapping(Base):
    """An approved, explainable ledger mapping."""

    __tablename__ = "accounting_mappings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_type: Mapped[AccountingMappingSourceType] = mapped_column(
        MAPPING_SOURCE_TYPE_ENUM, nullable=False
    )
    match_type: Mapped[AccountingMappingMatchType] = mapped_column(
        MAPPING_MATCH_TYPE_ENUM, nullable=False
    )
    #: Identity evidence. At most one of vendor/counterparty/narration is needed.
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    counterparty_name: Mapped[str | None] = mapped_column(String(255))
    narration_pattern: Mapped[str | None] = mapped_column(String(512))
    #: Free-text context such as RECEIPT, PAYMENT or PURCHASE.
    transaction_context: Mapped[str | None] = mapped_column(String(64))
    voucher_type: Mapped[str | None] = mapped_column(String(32))
    ledger_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledgers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    #: Human-readable explanation shown next to the suggestion.
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
            f"<AccountingMapping id={self.id} company_id={self.company_id} "
            f"source={self.source_type.value} match={self.match_type.value} "
            f"ledger_id={self.ledger_id}>"
        )


__all__ = [
    "AccountingMapping",
    "AccountingMappingMatchType",
    "AccountingMappingSourceType",
    "MAPPING_MATCH_TYPE_ENUM",
    "MAPPING_SOURCE_TYPE_ENUM",
]
