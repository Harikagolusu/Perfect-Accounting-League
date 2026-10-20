"""Pydantic v2 schemas for the Invoice Processing foundation.

`company_id` is never accepted here: it comes from the URL, so a client cannot
create an invoice against a company it did not address. Financial values are
`Decimal`, never float, and are carried as strings over the wire so no
binary-floating-point rounding happens in the browser either.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from app.models.invoice import InvoiceSource, InvoiceStatus
from app.schemas.common import TimestampedResponse, clean_optional

# 15 total digits with 2 decimal places, mirroring the NUMERIC column.
Money = Annotated[Decimal, Field(max_digits=15, decimal_places=2)]
#: A non-negative amount. `round_off` is deliberately excluded elsewhere: it may
#: legitimately be negative when a total is rounded down.
NonNegativeMoney = Annotated[Decimal, Field(max_digits=15, decimal_places=2, ge=0)]

# Emit money as a string. Keeps "1180.50" exact instead of 1180.4999999999998.
MoneyOut = Annotated[Decimal, PlainSerializer(lambda v: str(v), return_type=str)]


class _InvoiceFields(BaseModel):
    """Fields a client may write.

    `extra="forbid"` keeps internal columns (id, timestamps, company_id) from
    being smuggled in through a request body.
    """

    model_config = ConfigDict(extra="forbid")

    invoice_number: str | None = Field(default=None, max_length=128)
    invoice_date: date | None = None
    due_date: date | None = None

    vendor_name: str | None = Field(default=None, max_length=255)
    vendor_gstin: str | None = Field(default=None, max_length=15)
    vendor_pan: str | None = Field(default=None, max_length=10)
    buyer_gstin: str | None = Field(default=None, max_length=15)
    po_number: str | None = Field(default=None, max_length=128)

    currency: str = Field(default="INR", min_length=3, max_length=3)

    taxable_amount: NonNegativeMoney | None = None
    cgst_amount: NonNegativeMoney | None = None
    sgst_amount: NonNegativeMoney | None = None
    igst_amount: NonNegativeMoney | None = None
    cess_amount: NonNegativeMoney | None = None
    # May be negative: rounding a total down is normal.
    round_off: Money | None = None
    total_amount: NonNegativeMoney | None = None

    @field_validator("invoice_number", "vendor_name", "po_number", mode="before")
    @classmethod
    def _normalise_text(cls, value: Any) -> str | None:
        """Trim the edges only.

        Invoice identifiers carry meaningful internal formatting, so nothing
        beyond surrounding whitespace and blank-to-null is applied.
        """
        return clean_optional(value)

    @field_validator("vendor_gstin", "vendor_pan", "buyer_gstin", mode="before")
    @classmethod
    def _normalise_codes(cls, value: Any) -> str | None:
        """Tax identifiers are uppercase by convention."""
        cleaned = clean_optional(value)
        return cleaned.upper() if cleaned else None

    @field_validator("currency", mode="before")
    @classmethod
    def _normalise_currency(cls, value: Any) -> str:
        cleaned = clean_optional(value)
        return (cleaned or "INR").upper()


class InvoiceCreate(_InvoiceFields):
    """Payload for POST /api/v1/companies/{company_id}/invoices.

    `document_id` is required: every invoice must point at the file it came
    from. `vendor_id` is optional; no vendor is ever created automatically.
    """

    document_id: uuid.UUID
    vendor_id: uuid.UUID | None = None
    # A new invoice starts as a draft. Status is not client-assignable on
    # create: it is reached through the documented lifecycle.
    status: InvoiceStatus = InvoiceStatus.DRAFT


class InvoiceUpdate(_InvoiceFields):
    """Payload for PATCH /api/v1/companies/{company_id}/invoices/{invoice_id}.

    Every field is optional; only those actually sent are applied. `status`
    may only move to a state the lifecycle allows from the current one.
    """

    vendor_id: uuid.UUID | None = None
    status: InvoiceStatus | None = None


class InvoiceResponse(TimestampedResponse):
    """Invoice as returned by the API."""

    id: uuid.UUID
    company_id: uuid.UUID
    document_id: uuid.UUID
    vendor_id: uuid.UUID | None

    invoice_number: str | None
    invoice_date: date | None
    due_date: date | None
    vendor_name: str | None
    vendor_gstin: str | None
    vendor_pan: str | None
    buyer_gstin: str | None
    po_number: str | None
    currency: str

    taxable_amount: MoneyOut | None
    cgst_amount: MoneyOut | None
    sgst_amount: MoneyOut | None
    igst_amount: MoneyOut | None
    cess_amount: MoneyOut | None
    round_off: MoneyOut | None
    total_amount: MoneyOut | None

    status: InvoiceStatus
    source: InvoiceSource


class ProcessInvoiceResponse(BaseModel):
    """Result of asking the system to extract an invoice automatically.

    Only normalised, safe fields are exposed. The raw provider response and the
    stored file location never appear here.
    """

    document_id: uuid.UUID
    invoice_id: uuid.UUID | None = None
    configured: bool
    status: Literal["PROCESSED", "NEEDS_REVIEW", "FAILED", "NOT_CONFIGURED"]
    reason: str | None = None
    message: str
    issues: list[str] = Field(default_factory=list)
    is_invoice: bool | None = None
    provider: str | None = None
    model: str | None = None
    usage: dict[str, int] | None = None


__all__ = [
    "InvoiceCreate",
    "InvoiceResponse",
    "InvoiceUpdate",
    "ProcessInvoiceResponse",
]