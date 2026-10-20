"""Strict schema and deterministic validation for AI invoice extraction.

The AI response is never trusted as free text. It must parse as JSON, fit the
`ExtractedInvoice` model, and then pass deterministic structural checks here.

Nothing in this module applies tax law, calculates a tax amount, or repairs a
value. Problems become validation issues that a human resolves; the extracted
values themselves stay as read.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.schemas.common import GSTIN_RE, PAN_RE, clean_optional
from app.services.invoice_validation import validate_invoice_fields

#: Mirrors the NUMERIC(15,2) columns. A response outside this shape is rejected
#: as invalid output rather than rounded into something different.
ExtractedMoney = Annotated[
    Decimal,
    Field(max_digits=15, decimal_places=2, allow_inf_nan=False),
]

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

#: Accepted printed-date layouts. Day-first is the Indian invoice convention;
#: ISO is tried first because the prompt asks for it.
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
)


class InvalidExtractionOutput(Exception):
    """The provider response could not be read as an extraction payload."""

    message = "The AI response could not be read as structured invoice data."


class ExtractedInvoice(BaseModel):
    """The exact shape the model is asked to return.

    Text fields stay strings until deterministic validation converts dates and
    checks formats. Monetary fields are `Decimal`; the JSON parser is set up so
    a numeric literal becomes an exact Decimal, never a float.
    """

    model_config = ConfigDict(extra="ignore")

    is_invoice: bool

    invoice_number: str | None = Field(default=None, max_length=128)
    invoice_date: str | None = Field(default=None, max_length=64)
    due_date: str | None = Field(default=None, max_length=64)

    vendor_name: str | None = Field(default=None, max_length=255)
    vendor_gstin: str | None = Field(default=None, max_length=64)
    vendor_pan: str | None = Field(default=None, max_length=64)
    buyer_gstin: str | None = Field(default=None, max_length=64)
    po_number: str | None = Field(default=None, max_length=128)

    currency: str | None = Field(default=None, max_length=16)

    taxable_amount: ExtractedMoney | None = None
    cgst_amount: ExtractedMoney | None = None
    sgst_amount: ExtractedMoney | None = None
    igst_amount: ExtractedMoney | None = None
    cess_amount: ExtractedMoney | None = None
    round_off: ExtractedMoney | None = None
    total_amount: ExtractedMoney | None = None

    @field_validator(
        "invoice_number",
        "invoice_date",
        "due_date",
        "vendor_name",
        "vendor_gstin",
        "vendor_pan",
        "buyer_gstin",
        "po_number",
        "currency",
        mode="before",
    )
    @classmethod
    def _clean_text(cls, value: Any) -> str | None:
        return clean_optional(value)

    @field_validator("vendor_gstin", "vendor_pan", "buyer_gstin", mode="after")
    @classmethod
    def _upper_tax_ids(cls, value: str | None) -> str | None:
        return value.upper() if value else None

    @field_validator("currency", mode="after")
    @classmethod
    def _upper_currency(cls, value: str | None) -> str | None:
        return value.upper() if value else None


@dataclass(frozen=True)
class ValidatedExtraction:
    """Extracted values that are safe to persist, plus review issues."""

    values: dict[str, object]
    issues: tuple[str, ...]

    @property
    def has_issues(self) -> bool:
        return bool(self.issues)


def parse_extraction_payload(text: str) -> ExtractedInvoice:
    """Parse the provider's text into the strict extraction model.

    Raises `InvalidExtractionOutput` for empty text, non-JSON content, a
    non-object payload, NaN/Infinity numbers, or schema mismatches. Only a
    safe, fixed message is exposed.
    """
    if not text or not text.strip():
        raise InvalidExtractionOutput(InvalidExtractionOutput.message)

    cleaned = _strip_optional_fence(text.strip())
    try:
        data = json.loads(
            cleaned,
            parse_float=Decimal,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, ValueError):
        raise InvalidExtractionOutput(InvalidExtractionOutput.message) from None

    if not isinstance(data, dict):
        raise InvalidExtractionOutput(InvalidExtractionOutput.message)

    try:
        return ExtractedInvoice.model_validate(data)
    except ValidationError:
        # Pydantic's details include input values; keep them out of the
        # client-facing message and the log.
        raise InvalidExtractionOutput(InvalidExtractionOutput.message) from None


def validate_extraction(payload: ExtractedInvoice) -> ValidatedExtraction:
    """Convert and structurally check extracted values without changing them.

    Returns every issue found so a reviewer can resolve them in one pass.
    """
    issues: list[str] = []

    invoice_date, date_issue = _parse_date(payload.invoice_date, "Invoice date")
    if date_issue:
        issues.append(date_issue)

    due_date, due_issue = _parse_date(payload.due_date, "Due date")
    if due_issue:
        issues.append(due_issue)

    vendor_gstin = _bounded_identifier(
        payload.vendor_gstin, 15, "Vendor GSTIN", issues
    )
    if vendor_gstin and not GSTIN_RE.match(vendor_gstin):
        issues.append("Vendor GSTIN does not match the expected GSTIN format.")

    vendor_pan = _bounded_identifier(payload.vendor_pan, 10, "Vendor PAN", issues)
    if vendor_pan and not PAN_RE.match(vendor_pan):
        issues.append("Vendor PAN does not match the expected PAN format.")

    buyer_gstin = _bounded_identifier(
        payload.buyer_gstin, 15, "Buyer GSTIN", issues
    )
    if buyer_gstin and not GSTIN_RE.match(buyer_gstin):
        issues.append("Buyer GSTIN does not match the expected GSTIN format.")

    currency = payload.currency
    if currency and not _CURRENCY_RE.match(currency):
        # Do not persist a value the 3-character column cannot hold honestly.
        issues.append("Currency is not a three-letter ISO code.")
        currency = None

    # Structural checks shared with manual entry. Tax law is deliberately not
    # part of these: no GST rates, no TDS.
    shared = validate_invoice_fields(
        invoice_number=payload.invoice_number,
        invoice_date=invoice_date,
        due_date=due_date,
        vendor_gstin=vendor_gstin,
        vendor_pan=vendor_pan,
        buyer_gstin=buyer_gstin,
        taxable_amount=payload.taxable_amount,
        cgst_amount=payload.cgst_amount,
        sgst_amount=payload.sgst_amount,
        igst_amount=payload.igst_amount,
        cess_amount=payload.cess_amount,
        round_off=payload.round_off,
        total_amount=payload.total_amount,
    )
    issues.extend(shared.issues)

    if all(
        value is None
        for value in (
            payload.invoice_number,
            invoice_date,
            payload.vendor_name,
            payload.total_amount,
        )
    ):
        issues.append("No invoice fields could be read from the document.")

    values: dict[str, object] = {
        "invoice_number": payload.invoice_number,
        "invoice_date": invoice_date,
        "due_date": due_date,
        "vendor_name": payload.vendor_name,
        "vendor_gstin": vendor_gstin,
        "vendor_pan": vendor_pan,
        "buyer_gstin": buyer_gstin,
        "po_number": payload.po_number,
        "currency": currency,
        "taxable_amount": payload.taxable_amount,
        "cgst_amount": payload.cgst_amount,
        "sgst_amount": payload.sgst_amount,
        "igst_amount": payload.igst_amount,
        "cess_amount": payload.cess_amount,
        "round_off": payload.round_off,
        "total_amount": payload.total_amount,
    }

    return ValidatedExtraction(values=values, issues=tuple(issues))


def _bounded_identifier(
    value: str | None,
    max_length: int,
    label: str,
    issues: list[str],
) -> str | None:
    """Keep an identifier only if it fits its database column.

    A longer value would be truncated by the column, silently changing what
    the invoice said. It is reported and left out instead.
    """
    if value is None:
        return None
    if len(value) > max_length:
        issues.append(
            f"{label} is longer than {max_length} characters and was not saved."
        )
        return None
    return value


def _parse_date(raw: str | None, label: str) -> tuple[date | None, str | None]:
    """Parse a printed date; report rather than guess when it cannot be read."""
    if raw is None:
        return None, None
    value = raw.strip()
    if not value:
        return None, None

    iso = _ISO_DATE_RE.match(value)
    if iso:
        try:
            return date.fromisoformat(iso.group(0)), None
        except ValueError:
            pass

    for date_format in _DATE_FORMATS:
        try:
            return datetime.strptime(value, date_format).date(), None
        except ValueError:
            continue

    return None, f"{label} could not be parsed: {value!r}."


def _strip_optional_fence(text: str) -> str:
    """Remove one surrounding ```json fence if the model added one.

    This is tolerant parsing only: the content itself is still validated
    against the schema and never corrected.
    """
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"Invalid JSON constant: {value}")


__all__ = [
    "ExtractedInvoice",
    "InvalidExtractionOutput",
    "ValidatedExtraction",
    "parse_extraction_payload",
    "validate_extraction",
]
