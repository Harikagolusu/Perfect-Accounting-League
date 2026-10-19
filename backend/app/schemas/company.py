"""Pydantic v2 schemas for the Company resource.

Normalisation happens here rather than in the service or router, so the same
rules apply no matter which endpoint received the data.
"""

import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.company import CompanyStatus

# Mirrors the frontend's EMAIL_RE / GSTIN_RE / PAN_RE in src/lib/format.ts so
# both sides accept and reject exactly the same values. Pattern-only checks;
# GSTIN checksum verification is not implemented.
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def _clean_optional(value: Any) -> str | None:
    """Trim, and treat an empty string as "not supplied"."""
    if value is None:
        return None
    trimmed = str(value).strip()
    return trimmed or None


class _CompanyFields(BaseModel):
    """Shared field set, normalisation and format checks.

    Inherited by CompanyCreate and CompanyUpdate so the two cannot drift apart.
    """

    model_config = ConfigDict(extra="forbid")

    legal_name: str | None = Field(default=None, max_length=255)
    gstin: str | None = Field(default=None, max_length=15)
    pan: str | None = Field(default=None, max_length=10)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    address: str | None = None
    financial_year: str | None = Field(default=None, max_length=32)

    @field_validator("legal_name", "phone", "address", "financial_year", mode="before")
    @classmethod
    def _normalise_text(cls, value: Any) -> str | None:
        return _clean_optional(value)

    @field_validator("gstin", "pan", mode="before")
    @classmethod
    def _normalise_tax_ids(cls, value: Any) -> str | None:
        """Tax identifiers are uppercase by convention; normalise on input."""
        cleaned = _clean_optional(value)
        return cleaned.upper() if cleaned else None

    @field_validator("gstin")
    @classmethod
    def _check_gstin(cls, value: str | None) -> str | None:
        if value is not None and not GSTIN_RE.match(value):
            raise ValueError("Invalid GSTIN format")
        return value

    @field_validator("pan")
    @classmethod
    def _check_pan(cls, value: str | None) -> str | None:
        if value is not None and not PAN_RE.match(value):
            raise ValueError("Invalid PAN format")
        return value

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str | None) -> str | None:
        cleaned = _clean_optional(value)
        if cleaned is not None and not EMAIL_RE.match(cleaned):
            raise ValueError("Invalid email address")
        return cleaned


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
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Name must not be blank")
        return trimmed


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
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Name must not be blank")
        return trimmed


class CompanyResponse(BaseModel):
    """Company as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    legal_name: str | None
    gstin: str | None
    pan: str | None
    email: str | None
    phone: str | None
    address: str | None
    financial_year: str | None
    status: CompanyStatus
    created_at: datetime
    updated_at: datetime