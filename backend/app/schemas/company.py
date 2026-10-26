"""Pydantic v2 schemas for the Company resource.

Normalisation happens here rather than in the service or router, so the same
rules apply no matter which endpoint received the data. The shared rules live
in `app.schemas.common`.
"""

import uuid
from typing import Any

from pydantic import Field, field_validator

from app.models.company import CompanyStatus
from app.schemas.common import (
    NormalisedEmail,
    NormalisedTaxIds,
    TimestampedResponse,
    clean_optional,
)


def _not_blank(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("Name must not be blank")
    return trimmed


class _CompanyFields(NormalisedTaxIds, NormalisedEmail):
    """Shared field set for CompanyCreate and CompanyUpdate.

    Inherited by both so the two cannot drift apart.
    """

    legal_name: str | None = Field(default=None, max_length=255)
    gstin: str | None = Field(default=None, max_length=15)
    pan: str | None = Field(default=None, max_length=10)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    address: str | None = None
    cgst_ledger: str | None = Field(default=None, max_length=255)
    sgst_ledger: str | None = Field(default=None, max_length=255)
    igst_ledger: str | None = Field(default=None, max_length=255)
    cess_ledger: str | None = Field(default=None, max_length=255)
    round_off_ledger: str | None = Field(default=None, max_length=255)
    sales_ledger: str | None = Field(default=None, max_length=255)
    financial_year: str | None = Field(default=None, max_length=32)

    @field_validator(
        "legal_name",
        "phone",
        "address",
        "cgst_ledger",
        "sgst_ledger",
        "igst_ledger",
        "cess_ledger",
        "round_off_ledger",
        "sales_ledger",
        "financial_year",
        mode="before",
    )
    @classmethod
    def _normalise_text(cls, value: Any) -> str | None:
        return clean_optional(value)


class CompanyCreate(_CompanyFields):
    """Payload for POST /api/v1/companies.

    `name` becomes required by narrowing the optional field inherited above.
    """

    name: str = Field(max_length=255, description="Display name of the company.")
    status: CompanyStatus = Field(
        default=CompanyStatus.ACTIVE,
        description="Lifecycle state. Defaults to ACTIVE when not supplied.",
    )

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        return _not_blank(value)


class CompanyUpdate(_CompanyFields):
    """Payload for PATCH /api/v1/companies/{company_id}.

    Every field is optional; only those actually sent are applied, so a client
    can send `{"phone": null}` without wiping the rest of the record.
    """

    name: str | None = Field(default=None, max_length=255)
    status: CompanyStatus | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str | None) -> str | None:
        return None if value is None else _not_blank(value)


class CompanyResponse(TimestampedResponse):
    """Company as returned by the API."""

    id: uuid.UUID
    name: str
    legal_name: str | None
    gstin: str | None
    pan: str | None
    email: str | None
    phone: str | None
    address: str | None
    cgst_ledger: str | None
    sgst_ledger: str | None
    igst_ledger: str | None
    cess_ledger: str | None
    round_off_ledger: str | None
    sales_ledger: str | None
    financial_year: str | None
    status: CompanyStatus