"""Pydantic v2 schemas for the Vendor Master.

`company_id` is deliberately absent: it is taken from the URL path, never from
the request body, so a client cannot create a vendor against a company it did
not address.

Address and pincode are modelled because the client invoice and bank voucher
templates carry them. They remain optional: a vendor whose only identity is a
GSTIN is still valid, and an export reports the missing field rather than
inventing one.
"""

import uuid
from typing import Any

from pydantic import Field, field_validator

from app.models.vendor import VendorStatus
from app.schemas.common import NormalisedTaxIds, TimestampedResponse, clean_optional


def _not_blank(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("Name must not be blank")
    return trimmed


class _VendorFields(NormalisedTaxIds):
    """Shared field set for VendorCreate and VendorUpdate."""

    gstin: str | None = Field(default=None, max_length=15)
    pan: str | None = Field(default=None, max_length=10)
    address: str | None = None
    pincode: str | None = Field(default=None, max_length=10)
    msme_registration: str | None = Field(default=None, max_length=64)
    default_expense_ledger: str | None = Field(default=None, max_length=255)
    creditor_ledger: str | None = Field(default=None, max_length=255)
    parent_group: str | None = Field(default=None, max_length=255)

    @field_validator(
        "address",
        "pincode",
        "msme_registration",
        "default_expense_ledger",
        "creditor_ledger",
        "parent_group",
        mode="before",
    )
    @classmethod
    def _normalise_text(cls, value: Any) -> str | None:
        return clean_optional(value)


class VendorCreate(_VendorFields):
    """Payload for POST /api/v1/companies/{company_id}/vendors.

    `name` becomes required by narrowing the optional field inherited above.
    """

    name: str = Field(max_length=255, description="Vendor name.")
    status: VendorStatus = Field(
        default=VendorStatus.ACTIVE,
        description="Lifecycle state. Defaults to ACTIVE when not supplied.",
    )

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        return _not_blank(value)


class VendorUpdate(_VendorFields):
    """Payload for PATCH /api/v1/companies/{company_id}/vendors/{vendor_id}.

    Every field is optional; only those actually sent are applied.
    """

    name: str | None = Field(default=None, max_length=255)
    status: VendorStatus | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str | None) -> str | None:
        return None if value is None else _not_blank(value)


class VendorResponse(TimestampedResponse):
    """Vendor as returned by the API, always carrying its company scope."""

    id: uuid.UUID
    company_id: uuid.UUID
    name: str
    gstin: str | None
    pan: str | None
    address: str | None
    pincode: str | None
    msme_registration: str | None
    default_expense_ledger: str | None
    creditor_ledger: str | None
    parent_group: str | None
    status: VendorStatus