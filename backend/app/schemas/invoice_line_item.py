"""Pydantic schemas for canonical invoice line items.

Line items are stored in their own table and are not shaped around any client
spreadsheet. The client invoice template maps several columns onto these
fields; the mapper lives in `app.services.exports.invoice_export`.

`company_id` and `invoice_id` are deliberately absent from write payloads: both
come from the URL, so a client cannot create a line against another company's
invoice.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from app.schemas.common import TimestampedResponse, clean_optional

Money = Annotated[Decimal, Field(max_digits=15, decimal_places=2, ge=0)]
Quantity = Annotated[Decimal, Field(max_digits=15, decimal_places=3, ge=0)]
GstRate = Annotated[Decimal, Field(max_digits=5, decimal_places=2, ge=0, le=100)]
MoneyOut = Annotated[Decimal, PlainSerializer(lambda v: str(v), return_type=str)]

SupplyType = Literal["GOODS", "SERVICE"]


class _LineItemFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_number: int | None = Field(default=None, ge=1)
    supply_type: SupplyType | None = None
    hsn_sac: str | None = Field(default=None, max_length=16)
    quantity: Quantity | None = None
    unit_rate: Money | None = None
    gst_rate: GstRate | None = None

    taxable_amount: Money | None = None
    igst_amount: Money | None = None
    cgst_amount: Money | None = None
    sgst_amount: Money | None = None
    cess_amount: Money | None = None
    total_amount: Money | None = None

    @field_validator("hsn_sac", mode="before")
    @classmethod
    def _normalise_hsn(cls, value: Any) -> str | None:
        cleaned = clean_optional(value)
        return cleaned.upper() if cleaned else None

    @field_validator("supply_type", mode="before")
    @classmethod
    def _normalise_supply_type(cls, value: Any) -> str | None:
        if isinstance(value, str):
            cleaned = value.strip().upper()
            return cleaned or None
        return value


class InvoiceLineItemCreate(_LineItemFields):
    """Payload for POST .../invoices/{invoice_id}/line-items."""

    description: str = Field(max_length=500)
    supply_type: SupplyType | None = None

    @field_validator("description", mode="before")
    @classmethod
    def _normalise_description(cls, value: Any) -> str:
        cleaned = clean_optional(value)
        if not cleaned:
            raise ValueError("Description is required")
        return cleaned


class InvoiceLineItemUpdate(_LineItemFields):
    """Payload for PATCH on a line item. Only supplied fields are applied."""

    description: str | None = Field(default=None, max_length=500)

    @field_validator("description", mode="before")
    @classmethod
    def _normalise_description(cls, value: Any) -> str | None:
        return clean_optional(value)


class InvoiceLineItemResponse(TimestampedResponse):
    """A canonical line item, scoped to its company and invoice."""

    id: uuid.UUID
    company_id: uuid.UUID
    invoice_id: uuid.UUID
    line_number: int
    description: str
    supply_type: SupplyType | None
    hsn_sac: str | None
    quantity: MoneyOut | None
    unit_rate: MoneyOut | None
    gst_rate: MoneyOut | None
    taxable_amount: MoneyOut | None
    igst_amount: MoneyOut | None
    cgst_amount: MoneyOut | None
    sgst_amount: MoneyOut | None
    cess_amount: MoneyOut | None
    total_amount: MoneyOut | None


__all__ = [
    "InvoiceLineItemCreate",
    "InvoiceLineItemResponse",
    "InvoiceLineItemUpdate",
]
