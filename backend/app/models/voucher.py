"""Draft-voucher domain and its append-only audit trail.

A voucher is a PAL accounting object. It is not a Tally or Zoho object: those
are external posting/export adapters for later phases. The same voucher can be
reviewed, approved, exported as the client's accounting workbook, and only then
marked as posted after an external reference is verified.

Money is `NUMERIC(15,2)` and `Decimal` in Python. Debit/credit totals are
computed from the lines, never stored as a second source of truth.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

MONEY = Numeric(15, 2)
QUANTITY = Numeric(15, 3)


class VoucherStatus(str, enum.Enum):
    """The client's posting-status vocabulary."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"
    POSTING_IN_PROGRESS = "POSTING_IN_PROGRESS"
    POSTED = "POSTED"
    POSTING_FAILED = "POSTING_FAILED"
    AWAITING_IMPORT_CONFIRMATION = "AWAITING_IMPORT_CONFIRMATION"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class VoucherType(str, enum.Enum):
    """Voucher kinds explicitly described by the client process."""

    PURCHASE = "PURCHASE"
    SALES = "SALES"
    RECEIPT = "RECEIPT"
    PAYMENT = "PAYMENT"
    CONTRA = "CONTRA"
    JOURNAL = "JOURNAL"


class VoucherSourceType(str, enum.Enum):
    """What the voucher was derived from."""

    INVOICE = "INVOICE"
    BANK_TRANSACTION = "BANK_TRANSACTION"
    MANUAL = "MANUAL"


class VoucherSide(str, enum.Enum):
    """Debit or credit side of one ledger line."""

    DR = "Dr"
    CR = "Cr"


class AccountingEntityType(str, enum.Enum):
    """Entity types covered by the accounting audit trail."""

    VOUCHER = "VOUCHER"
    LEDGER = "LEDGER"
    MAPPING = "MAPPING"


VOUCHER_STATUS_ENUM = SAEnum(
    VoucherStatus,
    name="voucher_status",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)
VOUCHER_TYPE_ENUM = SAEnum(
    VoucherType,
    name="voucher_type",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)
VOUCHER_SOURCE_TYPE_ENUM = SAEnum(
    VoucherSourceType,
    name="voucher_source_type",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)
VOUCHER_SIDE_ENUM = SAEnum(
    VoucherSide,
    name="voucher_side",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)
ACCOUNTING_ENTITY_TYPE_ENUM = SAEnum(
    AccountingEntityType,
    name="accounting_entity_type",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class Voucher(Base):
    """One accounting voucher with one or more ledger lines."""

    __tablename__ = "vouchers"
    __table_args__ = (
        # A source document can produce exactly one voucher. This is the
        # database-level idempotency guard for repeated extraction/reprocessing.
        UniqueConstraint(
            "company_id",
            "source_type",
            "source_id",
            name="uq_vouchers_company_source",
        ),
        UniqueConstraint(
            "company_id",
            "voucher_type",
            "voucher_number",
            name="uq_vouchers_company_type_number",
        ),
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
    voucher_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    voucher_type: Mapped[VoucherType] = mapped_column(
        VOUCHER_TYPE_ENUM, nullable=False, index=True
    )
    #: Controlled, deterministic per company + voucher type.
    voucher_number: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[VoucherStatus] = mapped_column(
        VOUCHER_STATUS_ENUM,
        nullable=False,
        default=VoucherStatus.DRAFT,
        server_default=VoucherStatus.DRAFT.value,
        index=True,
    )
    narration: Mapped[str | None] = mapped_column(Text)

    #: Traceability. `source_id` is the invoice or bank-transaction UUID.
    #: It intentionally has no generic foreign key; the service validates the
    #: source within the same company before writing it.
    source_type: Mapped[VoucherSourceType] = mapped_column(
        VOUCHER_SOURCE_TYPE_ENUM,
        nullable=False,
        default=VoucherSourceType.MANUAL,
        server_default=VoucherSourceType.MANUAL.value,
        index=True,
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default="INR"
    )

    created_by: Mapped[str] = mapped_column(
        String(255), nullable=False, default="local-user", server_default="local-user"
    )
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_by: Mapped[str | None] = mapped_column(String(255))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    #: Direct-posting / import-verification evidence. A generated workbook never
    #: sets `posted_at`; only a verified posting reference does.
    posting_reference: Mapped[str | None] = mapped_column(String(255))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posting_error: Mapped[str | None] = mapped_column(Text)
    external_tally_voucher_id: Mapped[str | None] = mapped_column(String(255))
    external_zoho_voucher_id: Mapped[str | None] = mapped_column(String(255))

    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exported_by: Mapped[str | None] = mapped_column(String(255))
    export_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    #: Last deterministic validation result. It is a snapshot for the reviewer;
    #: validation is always recomputed from the lines before approval.
    validation_issues: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    lines: Mapped[list["VoucherLine"]] = relationship(
        back_populates="voucher",
        cascade="all, delete-orphan",
        order_by="VoucherLine.line_number",
        lazy="selectin",
    )

    @property
    def total_debit(self) -> Decimal:
        return sum(
            (line.amount for line in self.lines if line.side is VoucherSide.DR),
            Decimal("0"),
        )

    @property
    def total_credit(self) -> Decimal:
        return sum(
            (line.amount for line in self.lines if line.side is VoucherSide.CR),
            Decimal("0"),
        )

    @property
    def is_balanced(self) -> bool:
        return self.total_debit == self.total_credit

    def __repr__(self) -> str:
        return (
            f"<Voucher id={self.id} company_id={self.company_id} "
            f"number={self.voucher_number!r} type={self.voucher_type.value} "
            f"status={self.status.value}>"
        )


class VoucherLine(Base):
    """One ledger line of a voucher.

    `ledger_name` is a snapshot so an approved voucher remains readable after a
    ledger is renamed. `ledger_id` points at the company's Ledger Master when a
    matching active ledger exists; a missing master record is a validation
    exception, never a guessed ledger.
    """

    __tablename__ = "voucher_lines"
    __table_args__ = (
        UniqueConstraint(
            "voucher_id", "line_number", name="uq_voucher_lines_voucher_line_number"
        ),
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
    voucher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vouchers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    ledger_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledgers.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    ledger_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    side: Mapped[VoucherSide] = mapped_column(VOUCHER_SIDE_ENUM, nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    narration: Mapped[str | None] = mapped_column(Text)
    #: LEDGER, TAX or ITEM. The client's accounting workbook has one row per
    #: ledger line; item details are optional fields on that row.
    line_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="LEDGER", server_default="LEDGER"
    )

    item_name: Mapped[str | None] = mapped_column(String(255))
    hsn_sac: Mapped[str | None] = mapped_column(String(16))
    billed_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY)
    item_rate: Mapped[Decimal | None] = mapped_column(MONEY)
    item_rate_per: Mapped[str | None] = mapped_column(String(16))
    item_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    tax_type: Mapped[str | None] = mapped_column(String(32))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    voucher: Mapped[Voucher] = relationship(back_populates="lines")

    def __repr__(self) -> str:
        return (
            f"<VoucherLine id={self.id} voucher_id={self.voucher_id} "
            f"ledger={self.ledger_name!r} side={self.side.value} "
            f"amount={self.amount}>"
        )


class AccountingAuditEvent(Base):
    """Append-only audit event for accounting decisions.

    The table is never updated by the application. It records source, edits,
    ledger changes, approval/rejection, exports and posting verification.
    Secrets and provider responses are deliberately not stored.
    """

    __tablename__ = "accounting_audit_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[AccountingEntityType] = mapped_column(
        ACCOUNTING_ENTITY_TYPE_ENUM, nullable=False, index=True
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    voucher_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vouchers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(
        String(255), nullable=False, default="local-user", server_default="local-user"
    )
    previous_status: Mapped[str | None] = mapped_column(String(64))
    new_status: Mapped[str | None] = mapped_column(String(64))
    field: Mapped[str | None] = mapped_column(String(255))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    #: Safe structured context (line numbers, source ids, counts). Never secrets.
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    def __repr__(self) -> str:
        return (
            f"<AccountingAuditEvent id={self.id} company_id={self.company_id} "
            f"event={self.event_type!r} entity={self.entity_type.value}>"
        )


__all__ = [
    "ACCOUNTING_ENTITY_TYPE_ENUM",
    "AccountingAuditEvent",
    "AccountingEntityType",
    "VOUCHER_SIDE_ENUM",
    "VOUCHER_SOURCE_TYPE_ENUM",
    "VOUCHER_STATUS_ENUM",
    "VOUCHER_TYPE_ENUM",
    "Voucher",
    "VoucherLine",
    "VoucherSide",
    "VoucherSourceType",
    "VoucherStatus",
    "VoucherType",
]
