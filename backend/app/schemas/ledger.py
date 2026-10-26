"""Pydantic schemas for the company-scoped Ledger Master."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.ledger import LedgerSource
from app.schemas.common import TimestampedResponse, clean_optional


class _LedgerFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, max_length=64)
    ledger_type: str | None = Field(default=None, max_length=64)
    parent_group: str | None = Field(default=None, max_length=255)
    tax_classification: str | None = Field(default=None, max_length=64)
    source: LedgerSource = LedgerSource.MANUAL
    external_tally_id: str | None = Field(default=None, max_length=255)
    external_zoho_id: str | None = Field(default=None, max_length=255)

    @field_validator(
        "code",
        "ledger_type",
        "parent_group",
        "tax_classification",
        "external_tally_id",
        "external_zoho_id",
        mode="before",
    )
    @classmethod
    def _normalise_text(cls, value: Any) -> str | None:
        return clean_optional(value)


class LedgerCreate(_LedgerFields):
    name: str = Field(max_length=255)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Ledger name must not be blank")
        return cleaned


class LedgerUpdate(_LedgerFields):
    name: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Ledger name must not be blank")
        return cleaned


class LedgerResponse(TimestampedResponse):
    id: uuid.UUID
    company_id: uuid.UUID
    name: str
    code: str | None
    ledger_type: str | None
    parent_group: str | None
    tax_classification: str | None
    source: LedgerSource
    external_tally_id: str | None
    external_zoho_id: str | None
    is_active: bool


__all__ = ["LedgerCreate", "LedgerResponse", "LedgerUpdate"]
