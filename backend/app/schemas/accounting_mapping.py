"""Pydantic schemas for traceable accounting mappings."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.accounting_mapping import (
    AccountingMappingMatchType,
    AccountingMappingSourceType,
)
from app.schemas.common import TimestampedResponse, clean_optional


class _MappingFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: AccountingMappingSourceType
    match_type: AccountingMappingMatchType
    vendor_id: uuid.UUID | None = None
    counterparty_name: str | None = Field(default=None, max_length=255)
    narration_pattern: str | None = Field(default=None, max_length=512)
    transaction_context: str | None = Field(default=None, max_length=64)
    voucher_type: str | None = Field(default=None, max_length=32)
    ledger_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator(
        "counterparty_name",
        "narration_pattern",
        "transaction_context",
        "voucher_type",
        mode="before",
    )
    @classmethod
    def _normalise_text(cls, value: Any) -> str | None:
        return clean_optional(value)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("A reason is required for an accounting mapping")
        return cleaned


class AccountingMappingCreate(_MappingFields):
    @model_validator(mode="after")
    def _require_evidence(self) -> "AccountingMappingCreate":
        if self.match_type is AccountingMappingMatchType.VENDOR and self.vendor_id is None:
            raise ValueError("A VENDOR mapping requires vendor_id")
        if (
            self.match_type is AccountingMappingMatchType.COUNTERPARTY
            and not self.counterparty_name
        ):
            raise ValueError("A COUNTERPARTY mapping requires counterparty_name")
        if self.match_type is AccountingMappingMatchType.NARRATION and not self.narration_pattern:
            raise ValueError("A NARRATION mapping requires narration_pattern")
        return self


class AccountingMappingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    counterparty_name: str | None = Field(default=None, max_length=255)
    narration_pattern: str | None = Field(default=None, max_length=512)
    transaction_context: str | None = Field(default=None, max_length=64)
    voucher_type: str | None = Field(default=None, max_length=32)
    ledger_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None

    @field_validator(
        "counterparty_name",
        "narration_pattern",
        "transaction_context",
        "voucher_type",
        mode="before",
    )
    @classmethod
    def _normalise_text(cls, value: Any) -> str | None:
        return clean_optional(value)


class AccountingMappingResponse(TimestampedResponse):
    id: uuid.UUID
    company_id: uuid.UUID
    source_type: AccountingMappingSourceType
    match_type: AccountingMappingMatchType
    vendor_id: uuid.UUID | None
    counterparty_name: str | None
    narration_pattern: str | None
    transaction_context: str | None
    voucher_type: str | None
    ledger_id: uuid.UUID
    reason: str
    approved_by: str | None
    approved_at: datetime | None
    is_active: bool


__all__ = [
    "AccountingMappingCreate",
    "AccountingMappingResponse",
    "AccountingMappingUpdate",
]
