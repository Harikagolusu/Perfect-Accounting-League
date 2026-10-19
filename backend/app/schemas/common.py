"""Shared schema building blocks.

Normalisation and validation for tax identifiers and email live here so the
Company and Vendor schemas cannot drift apart. Field declarations stay in the
schemas that own the fields.

The mixins validate field names they do not declare themselves, so every
decorator sets `check_fields=False`: the rule applies to any subclass that
provides the field, and is inert for one that does not.
"""

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

# Mirrors the frontend's EMAIL_RE / GSTIN_RE / PAN_RE in src/lib/format.ts so
# both sides accept and reject exactly the same values. Pattern-only checks;
# GSTIN checksum verification is not implemented.
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def clean_optional(value: Any) -> str | None:
    """Trim, and treat an empty string as "not supplied"."""
    if value is None:
        return None
    trimmed = str(value).strip()
    return trimmed or None


class NormalisedTaxIds(BaseModel):
    """For payloads that declare `gstin` and `pan`.

    Tax identifiers are uppercase by convention and blank means "not supplied".
    """

    model_config = ConfigDict(extra="forbid")

    @field_validator("gstin", "pan", mode="before", check_fields=False)
    @classmethod
    def _normalise_tax_ids(cls, value: Any) -> str | None:
        cleaned = clean_optional(value)
        return cleaned.upper() if cleaned else None

    @field_validator("gstin", check_fields=False)
    @classmethod
    def _check_gstin(cls, value: str | None) -> str | None:
        if value is not None and not GSTIN_RE.match(value):
            raise ValueError("Invalid GSTIN format")
        return value

    @field_validator("pan", check_fields=False)
    @classmethod
    def _check_pan(cls, value: str | None) -> str | None:
        if value is not None and not PAN_RE.match(value):
            raise ValueError("Invalid PAN format")
        return value


class NormalisedEmail(BaseModel):
    """For payloads that declare `email`."""

    @field_validator("email", check_fields=False)
    @classmethod
    def _check_email(cls, value: str | None) -> str | None:
        cleaned = clean_optional(value)
        if cleaned is not None and not EMAIL_RE.match(cleaned):
            raise ValueError("Invalid email address")
        return cleaned


class TimestampedResponse(BaseModel):
    """Adds the audit timestamps every stored entity carries."""

    model_config = ConfigDict(from_attributes=True)

    created_at: datetime
    updated_at: datetime