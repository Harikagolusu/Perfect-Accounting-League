"""Canonical invoice line items.

A line item belongs to exactly one invoice and one company. It is deliberately
separate from the invoice header: an invoice may have one line or many, and the
client invoice template has one row per line.

Nothing in this model is named after a spreadsheet column. Client columns are
mapped onto these fields by `app.services.exports.invoice_export`, so the
database stays canonical and a different output template can reuse the same
data.

Money is `NUMERIC(15,2)` and quantity is `NUMERIC(15,3)`, keeping both exact in
Python (`Decimal`) all the way to the export mapper.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

#: Mirrors the invoice money columns.
MONEY = Numeric(15, 2)
#: Quantities can legitimately be fractional (kg, metres, hours).
QUANTITY = Numeric(15, 3)
#: A GST rate is a percentage, not an amount.
GST_RATE = Numeric(5, 2)


class InvoiceLineItem(Base):
    """One line of an invoice."""

    __tablename__ = "invoice_line_items"
    __table_args__ = (
        UniqueConstraint(
            "invoice_id",
            "line_number",
            name="uq_invoice_line_items_invoice_line_number",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: Denormalised scope, so every read can filter on company_id directly.
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    #: A line has no meaning without its invoice, so deleting the invoice
    #: removes its lines.
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    #: GOODS or SERVICE. Stored as text so the enum can grow without a
    #: PostgreSQL type migration; the API validates the two values.
    supply_type: Mapped[str | None] = mapped_column(String(16))
    hsn_sac: Mapped[str | None] = mapped_column(String(16))
    quantity: Mapped[Decimal | None] = mapped_column(QUANTITY)
    gst_rate: Mapped[Decimal | None] = mapped_column(GST_RATE)

    taxable_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    igst_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    cgst_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    sgst_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    cess_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    total_amount: Mapped[Decimal | None] = mapped_column(MONEY)

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
            f"<InvoiceLineItem id={self.id} company_id={self.company_id} "
            f"invoice_id={self.invoice_id} line={self.line_number} "
            f"description={self.description!r}>"
        )
