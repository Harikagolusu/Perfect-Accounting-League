"""Invoice ORM model — structured accounting data for one invoice.

Domain separation
-----------------
A `Document` is the uploaded physical file. An `Invoice` is the structured
data read from or entered against it. A `Vendor` is the supplier master. These
stay separate:

* an invoice references its source document by `document_id`;
* it may reference a vendor master record by `vendor_id`, which stays optional;
* the supplier name/GSTIN/PAN *printed on the invoice* are stored as text on
  the invoice itself. They are what the document says, and must stay readable
  even when no vendor master record is (or ever will be) linked.

There are deliberately no line-item, ledger-mapping or export columns: those
belong to later, separate entities.

Money is `NUMERIC`, never float. Python-side values are `Decimal` all the way
through, so no binary-floating-point error can enter an accounting total.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Enum as SAEnum, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# 15 total digits, 2 decimal places. Comfortably above any realistic invoice
# value while staying well inside PostgreSQL's NUMERIC range.
MONEY = Numeric(15, 2)


class InvoiceStatus(str, enum.Enum):
    """Where an invoice sits in the review pipeline.

    An invoice is only `PROCESSED` once extraction and validation completed;
    merely having a row means nothing.
    """

    DRAFT = "DRAFT"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


class InvoiceSource(str, enum.Enum):
    """Where the invoice data came from.

    `MANUAL` means a person typed the values in. `AI` means the configured
    vision provider read them from the source document in Step 8.
    """

    MANUAL = "MANUAL"
    AI = "AI"


class VendorMappingStatus(str, enum.Enum):
    """How far the invoice has got in linking to a vendor master record.

    `SUGGESTED` is deliberately separate from `MATCHED`: a name-only match is
    not an accounting fact until a person confirms it. `REJECTED` prevents an
    automatic matcher from silently re-applying the suggestion on a retry.
    """

    UNMAPPED = "UNMAPPED"
    SUGGESTED = "SUGGESTED"
    MATCHED = "MATCHED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    AMBIGUOUS = "AMBIGUOUS"


class VendorMatchMethod(str, enum.Enum):
    """The deterministic evidence behind a vendor match.

    A user-confirmed mapping may carry the method of the suggestion that led to
    it, or no method when the vendor was chosen manually.
    """

    EXACT_GSTIN = "EXACT_GSTIN"
    EXACT_PAN = "EXACT_PAN"
    NORMALIZED_NAME = "NORMALIZED_NAME"


INVOICE_STATUS_ENUM = SAEnum(
    InvoiceStatus,
    name="invoice_status",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)

INVOICE_SOURCE_ENUM = SAEnum(
    InvoiceSource,
    name="invoice_source",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)

VENDOR_MAPPING_STATUS_ENUM = SAEnum(
    VendorMappingStatus,
    name="invoice_vendor_mapping_status",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)

VENDOR_MATCH_METHOD_ENUM = SAEnum(
    VendorMatchMethod,
    name="invoice_vendor_match_method",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class Invoice(Base):
    """Structured data for one invoice, belonging to exactly one company."""

    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Scoping: every read filters on company_id, exactly as Vendor and Document do.
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # The source artifact. Deleting an invoice never deletes the document.
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Optional link to the supplier master. Not auto-created, never guessed.
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # A pending name-based suggestion. Separate from vendor_id so a suggestion
    # is never mistaken for an assigned vendor by reports or exports.
    suggested_vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # The immediately previous assignment, kept only when a person explicitly
    # changes or clears a mapped vendor. This is deliberately one step of
    # history, not an approval workflow.
    previous_vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="SET NULL"),
        nullable=True,
    )
    vendor_mapping_status: Mapped[VendorMappingStatus] = mapped_column(
        VENDOR_MAPPING_STATUS_ENUM,
        nullable=False,
        default=VendorMappingStatus.UNMAPPED,
        server_default=VendorMappingStatus.UNMAPPED.value,
    )
    vendor_match_method: Mapped[VendorMatchMethod | None] = mapped_column(
        VENDOR_MATCH_METHOD_ENUM,
        nullable=True,
    )

    invoice_number: Mapped[str | None] = mapped_column(String(128))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)

    # As printed on the invoice. Not the vendor master.
    vendor_name: Mapped[str | None] = mapped_column(String(255))
    vendor_gstin: Mapped[str | None] = mapped_column(String(15))
    vendor_pan: Mapped[str | None] = mapped_column(String(10))
    buyer_gstin: Mapped[str | None] = mapped_column(String(15))
    po_number: Mapped[str | None] = mapped_column(String(128))

    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default="INR"
    )

    taxable_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    cgst_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    sgst_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    igst_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    cess_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    round_off: Mapped[Decimal | None] = mapped_column(MONEY)
    total_amount: Mapped[Decimal | None] = mapped_column(MONEY)

    status: Mapped[InvoiceStatus] = mapped_column(
        INVOICE_STATUS_ENUM,
        nullable=False,
        default=InvoiceStatus.DRAFT,
        server_default=InvoiceStatus.DRAFT.value,
    )
    source: Mapped[InvoiceSource] = mapped_column(
        INVOICE_SOURCE_ENUM,
        nullable=False,
        default=InvoiceSource.MANUAL,
        server_default=InvoiceSource.MANUAL.value,
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
            f"<Invoice id={self.id} company_id={self.company_id} "
            f"document_id={self.document_id} number={self.invoice_number!r} "
            f"status={self.status.value}>"
        )
