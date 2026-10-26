"""Pydantic schemas for draft vouchers, review actions and audit events."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator

from app.models.voucher import (
    AccountingEntityType,
    VoucherSide,
    VoucherSourceType,
    VoucherStatus,
    VoucherType,
)
from app.schemas.common import TimestampedResponse, clean_optional

Money = Annotated[Decimal, Field(max_digits=15, decimal_places=2)]
MoneyOut = Annotated[Decimal, PlainSerializer(lambda v: str(v), return_type=str)]
Quantity = Annotated[Decimal, Field(max_digits=15, decimal_places=3)]


class VoucherLineInput(BaseModel):
    """One ledger row supplied by a reviewer or a split-bank request."""

    model_config = ConfigDict(extra="forbid")

    ledger_id: uuid.UUID | None = None
    #: Optional name snapshot. When ledger_id is supplied the service uses the
    #: master record's current name.
    ledger_name: str | None = Field(default=None, max_length=255)
    side: VoucherSide
    amount: Money = Field(gt=0)
    narration: str | None = None
    line_type: str = Field(default="LEDGER", max_length=32)
    item_name: str | None = Field(default=None, max_length=255)
    hsn_sac: str | None = Field(default=None, max_length=16)
    billed_quantity: Quantity | None = None
    item_rate: Money | None = None
    item_rate_per: str | None = Field(default=None, max_length=16)
    item_amount: Money | None = None
    tax_type: str | None = Field(default=None, max_length=32)

    @field_validator("ledger_name", "narration", "line_type", "item_name", "hsn_sac", "item_rate_per", "tax_type", mode="before")
    @classmethod
    def _normalise_text(cls, value: Any) -> str | None:
        return clean_optional(value)

    @model_validator(mode="after")
    def _require_ledger_reference(self) -> "VoucherLineInput":
        if self.ledger_id is None and not self.ledger_name:
            raise ValueError("Each voucher line needs a ledger_id or ledger_name")
        return self


class VoucherCreate(BaseModel):
    """Manual draft voucher creation."""

    model_config = ConfigDict(extra="forbid")

    voucher_date: date
    voucher_type: VoucherType
    narration: str | None = None
    currency: str = Field(default="INR", min_length=3, max_length=3)
    lines: list[VoucherLineInput] = Field(default_factory=list)

    @field_validator("narration", mode="before")
    @classmethod
    def _normalise_text(cls, value: Any) -> str | None:
        return clean_optional(value)

    @field_validator("currency", mode="before")
    @classmethod
    def _normalise_currency(cls, value: Any) -> str:
        return (clean_optional(value) or "INR").upper()


class VoucherUpdate(BaseModel):
    """Editable header and line fields. Status is changed by explicit actions."""

    model_config = ConfigDict(extra="forbid")

    voucher_date: date | None = None
    voucher_type: VoucherType | None = None
    narration: str | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    lines: list[VoucherLineInput] | None = None

    @field_validator("narration", mode="before")
    @classmethod
    def _normalise_text(cls, value: Any) -> str | None:
        return clean_optional(value)

    @field_validator("currency", mode="before")
    @classmethod
    def _normalise_currency(cls, value: Any) -> str | None:
        cleaned = clean_optional(value)
        return cleaned.upper() if cleaned else None


class VoucherLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    voucher_id: uuid.UUID
    line_number: int
    ledger_id: uuid.UUID | None
    ledger_name: str
    side: VoucherSide
    amount: MoneyOut
    narration: str | None
    line_type: str
    item_name: str | None
    hsn_sac: str | None
    billed_quantity: str | None
    item_rate: MoneyOut | None
    item_rate_per: str | None
    item_amount: MoneyOut | None
    tax_type: str | None

    @field_validator("billed_quantity", mode="before")
    @classmethod
    def _quantity_to_string(cls, value: Any) -> str | None:
        return None if value is None else str(value)


class VoucherIssueResponse(BaseModel):
    code: str
    message: str
    severity: Literal["ERROR", "WARNING"] = "ERROR"
    field: str | None = None


class VoucherResponse(TimestampedResponse):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    voucher_date: date
    voucher_type: VoucherType
    voucher_number: str
    status: VoucherStatus
    narration: str | None
    source_type: VoucherSourceType
    source_id: uuid.UUID | None
    source_document_id: uuid.UUID | None
    currency: str
    created_by: str
    approved_by: str | None
    approved_at: datetime | None
    rejected_by: str | None
    rejected_at: datetime | None
    rejection_reason: str | None
    posting_reference: str | None
    posted_at: datetime | None
    posting_error: str | None
    external_tally_voucher_id: str | None
    external_zoho_voucher_id: str | None
    exported_at: datetime | None
    exported_by: str | None
    export_count: int
    validation_issues: list[VoucherIssueResponse]
    total_debit: MoneyOut
    total_credit: MoneyOut
    is_balanced: bool
    lines: list[VoucherLineResponse]

    @field_validator("validation_issues", mode="before")
    @classmethod
    def _issues_default(cls, value: Any) -> Any:
        return value or []


class VoucherActionRequest(BaseModel):
    """Approval, rejection, export and verification actions.

    `actor` is a clean service boundary for the future authenticated identity.
    Phase 1 has no user table, so the caller supplies a display name; the router
    defaults it to `local-user`. No role is fabricated.
    """

    model_config = ConfigDict(extra="forbid")

    actor: str = Field(default="local-user", max_length=255)
    comment: str | None = Field(default=None, max_length=2000)
    reason: str | None = Field(default=None, max_length=2000)
    external_reference: str | None = Field(default=None, max_length=255)
    verified: bool | None = None

    @field_validator("actor", mode="before")
    @classmethod
    def _actor_not_blank(cls, value: Any) -> str:
        cleaned = clean_optional(value)
        return cleaned or "local-user"


class VoucherExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voucher_ids: list[uuid.UUID] | None = None
    date_from: date | None = None
    date_to: date | None = None
    actor: str = Field(default="local-user", max_length=255)


class VoucherFromInvoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = Field(default="local-user", max_length=255)


class VoucherFromBankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = Field(default="local-user", max_length=255)
    #: Optional split of the counterparty side. The bank side remains one line.
    split_lines: list[VoucherLineInput] | None = None


class LedgerSuggestion(BaseModel):
    """One explainable ledger suggestion."""

    purpose: str
    ledger_id: uuid.UUID | None
    ledger_name: str | None
    source: str
    reason: str
    code: str | None = None


class LedgerSuggestionResponse(BaseModel):
    suggestions: list[LedgerSuggestion] = Field(default_factory=list)
    exceptions: list[VoucherIssueResponse] = Field(default_factory=list)


class VoucherAuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    entity_type: AccountingEntityType
    entity_id: uuid.UUID | None
    voucher_id: uuid.UUID | None
    event_type: str
    actor: str
    previous_status: str | None
    new_status: str | None
    field: str | None
    old_value: str | None
    new_value: str | None
    reason: str | None
    event_metadata: dict[str, Any] | None
    created_at: datetime


__all__ = [
    "LedgerSuggestion",
    "LedgerSuggestionResponse",
    "VoucherActionRequest",
    "VoucherAuditEventResponse",
    "VoucherCreate",
    "VoucherExportRequest",
    "VoucherFromBankRequest",
    "VoucherFromInvoiceRequest",
    "VoucherIssueResponse",
    "VoucherLineInput",
    "VoucherLineResponse",
    "VoucherResponse",
    "VoucherUpdate",
]
