"""Deterministic vendor identity matching (Step 9).

The matcher answers one question: *given the supplier identity printed on an
invoice, which vendor master record, if any, is the same supplier?*

It is deliberately deterministic and explainable:

    exact GSTIN -> exact PAN -> normalized name -> no match / ambiguous

No fuzzy distance, embeddings, vector search or AI is involved. A false vendor
match is an accounting error, so an unclear case is reported as `AMBIGUOUS`
instead of being guessed.

Company isolation
-----------------
Every query is filtered by `company_id`. A vendor in another company is not a
candidate, even when the GSTIN or name is identical.

Historical reuse
----------------
A normalized-name match is only a *suggestion* until a user confirms it. Once a
confirmed invoice exists for the same company, vendor and normalized printed
name, that identity has been approved by a person; later invoices with that
identity may reuse it without asking again. The evidence is the invoice history
itself, so no parallel mapping table is introduced.
"""

from __future__ import annotations

import enum
import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.invoice import Invoice, VendorMappingStatus, VendorMatchMethod
from app.models.vendor import Vendor, VendorStatus
from app.schemas.common import GSTIN_RE, PAN_RE, clean_optional

#: Stable reason codes. Callers never have to match on prose.
MATCH_REASON_EXACT_GSTIN = "EXACT_GSTIN"
MATCH_REASON_EXACT_PAN = "EXACT_PAN"
MATCH_REASON_NORMALIZED_NAME = "NORMALIZED_NAME"
MATCH_REASON_HISTORICAL_NAME = "HISTORICAL_NAME"
MATCH_REASON_NO_MATCH = "NO_MATCH"
MATCH_REASON_NO_VENDOR_INFORMATION = "NO_VENDOR_INFORMATION"
MATCH_REASON_INVALID_IDENTIFIERS = "INVALID_IDENTIFIERS"
MATCH_REASON_MULTIPLE_GSTIN = "MULTIPLE_GSTIN_MATCHES"
MATCH_REASON_MULTIPLE_PAN = "MULTIPLE_PAN_MATCHES"
MATCH_REASON_IDENTIFIER_CONFLICT = "IDENTIFIER_CONFLICT"
MATCH_REASON_IDENTIFIER_NAME_CONFLICT = "IDENTIFIER_NAME_CONFLICT"
MATCH_REASON_MULTIPLE_NAME = "MULTIPLE_NAME_MATCHES"


class VendorMatchStatus(str, enum.Enum):
    """The three outcomes a deterministic match can produce."""

    MATCHED = "MATCHED"
    NO_MATCH = "NO_MATCH"
    AMBIGUOUS = "AMBIGUOUS"


#: Legal forms that may trail a company name. They are removed only from the
#: end, only as whole tokens, and only while a meaningful token remains. This
#: is what makes "ABC Traders Pvt Ltd" and "ABC Traders" the same identity
#: without turning "ABC Traders India" into "ABC Traders".
_LEGAL_SUFFIXES: tuple[tuple[str, ...], ...] = (
    ("private", "limited"),
    ("private", "ltd"),
    ("pvt", "limited"),
    ("pvt", "ltd"),
    ("public", "limited"),
    ("public", "ltd"),
    ("limited",),
    ("ltd",),
    ("llp",),
    ("incorporated",),
    ("inc",),
    ("corporation",),
    ("corp",),
    ("company",),
    ("co",),
)

_NON_WORD = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def normalize_vendor_name(value: str | None) -> str:
    """Return a conservative, deterministic identity for a vendor name.

    Handles case, repeated whitespace, punctuation, ``&``/``and`` and trailing
    legal forms. It never removes meaningful words such as "India", "Traders"
    or "Enterprises", so genuinely different suppliers stay different.

    The function is pure: the same input always yields the same output, with no
    database access.
    """
    if value is None:
        return ""

    text = unicodedata.normalize("NFKC", value).casefold()
    # "&" is a spelling of "and" in company names, not a meaningful character.
    text = text.replace("&", " and ")
    text = _NON_WORD.sub(" ", text)
    text = text.replace("_", " ")
    tokens = _WHITESPACE.sub(" ", text).strip().split(" ")
    tokens = [token for token in tokens if token]
    if not tokens:
        return ""

    tokens = _strip_trailing_legal_forms(tokens)
    return " ".join(tokens)


def _strip_trailing_legal_forms(tokens: list[str]) -> list[str]:
    """Remove trailing legal forms, one safe piece at a time.

    At least one token must remain, so a vendor literally called "Limited"
    keeps that name. A trailing "and" left behind by "& Co" is dropped too,
    because it no longer joins anything.
    """
    changed = True
    while changed and len(tokens) > 1:
        changed = False
        for suffix in _LEGAL_SUFFIXES:
            size = len(suffix)
            if len(tokens) - size < 1:
                continue
            if tuple(tokens[-size:]) == suffix:
                tokens = tokens[:-size]
                changed = True
                break
        if not changed and len(tokens) > 1 and tokens[-1] == "and":
            tokens = tokens[:-1]
            changed = True
    return tokens


def normalize_gstin(value: str | None) -> str | None:
    """Return an upper-cased GSTIN only when it matches the expected format."""
    cleaned = clean_optional(value)
    if cleaned is None:
        return None
    candidate = cleaned.upper()
    return candidate if GSTIN_RE.fullmatch(candidate) else None


def normalize_pan(value: str | None) -> str | None:
    """Return an upper-cased PAN only when it matches the expected format."""
    cleaned = clean_optional(value)
    if cleaned is None:
        return None
    candidate = cleaned.upper()
    return candidate if PAN_RE.fullmatch(candidate) else None


@dataclass(frozen=True)
class VendorCandidate:
    """Safe, minimal vendor information for a match result.

    No timestamps, status or private storage details: only what a reviewer
    needs to recognise the supplier and see its historical grouping.
    """

    id: uuid.UUID
    name: str
    gstin: str | None
    pan: str | None
    default_expense_ledger: str | None
    creditor_ledger: str | None
    parent_group: str | None

    @classmethod
    def from_vendor(cls, vendor: Vendor) -> "VendorCandidate":
        return cls(
            id=vendor.id,
            name=vendor.name,
            gstin=vendor.gstin,
            pan=vendor.pan,
            default_expense_ledger=vendor.default_expense_ledger,
            creditor_ledger=vendor.creditor_ledger,
            parent_group=vendor.parent_group,
        )


@dataclass(frozen=True)
class VendorMatchResult:
    """Outcome of matching one printed supplier identity.

    `requires_confirmation` is true for a name-only match that has not been
    confirmed before. GSTIN and PAN matches are strong identifiers and do not
    require confirmation.
    """

    status: VendorMatchStatus
    reason: str
    method: VendorMatchMethod | None = None
    vendor: VendorCandidate | None = None
    candidates: tuple[VendorCandidate, ...] = ()
    requires_confirmation: bool = False


def _candidates(vendors: Iterable[Vendor]) -> tuple[VendorCandidate, ...]:
    """Deduplicate candidates while preserving deterministic order."""
    seen: set[uuid.UUID] = set()
    result: list[VendorCandidate] = []
    for vendor in vendors:
        if vendor.id in seen:
            continue
        seen.add(vendor.id)
        result.append(VendorCandidate.from_vendor(vendor))
    return tuple(result)


def _matched(
    vendor: Vendor,
    *,
    method: VendorMatchMethod,
    reason: str,
    requires_confirmation: bool = False,
) -> VendorMatchResult:
    return VendorMatchResult(
        status=VendorMatchStatus.MATCHED,
        reason=reason,
        method=method,
        vendor=VendorCandidate.from_vendor(vendor),
        candidates=(VendorCandidate.from_vendor(vendor),),
        requires_confirmation=requires_confirmation,
    )


def _ambiguous(reason: str, vendors: Iterable[Vendor]) -> VendorMatchResult:
    return VendorMatchResult(
        status=VendorMatchStatus.AMBIGUOUS,
        reason=reason,
        candidates=_candidates(vendors),
    )


def _has_confirmed_name(
    db: Session,
    company_id: uuid.UUID,
    vendor_id: uuid.UUID,
    normalized_name: str,
) -> bool:
    """Whether this company confirmed this vendor for this printed name before.

    Only `CONFIRMED` invoices count. An automatic GSTIN match does not prove
    that a name-based identity is safe, and a rejected suggestion proves the
    opposite. The check is company- and vendor-scoped.
    """
    confirmed_names = db.scalars(
        select(Invoice.vendor_name).where(
            Invoice.company_id == company_id,
            Invoice.vendor_id == vendor_id,
            Invoice.vendor_mapping_status == VendorMappingStatus.CONFIRMED,
            Invoice.vendor_name.is_not(None),
        )
    ).all()
    return any(
        name is not None and normalize_vendor_name(name) == normalized_name
        for name in confirmed_names
    )


def match_vendor(
    db: Session,
    company_id: uuid.UUID,
    vendor_name: str | None,
    vendor_gstin: str | None,
    vendor_pan: str | None,
) -> VendorMatchResult:
    """Match a printed supplier identity against this company's vendors.

    Priority is exact GSTIN, exact PAN, then normalized name. Active and
    NEEDS_MAPPING vendors participate; INACTIVE (soft-deleted) vendors do not
    receive new automatic matches.

    Conflicts are never resolved by guessing. If an identifier points at one
    vendor and the name at another, the result is `AMBIGUOUS` with both
    candidates so a person can decide.
    """
    vendors = list(
        db.scalars(
            select(Vendor)
            .where(
                Vendor.company_id == company_id,
                Vendor.status != VendorStatus.INACTIVE,
            )
            .order_by(Vendor.name, Vendor.created_at)
        ).all()
    )

    cleaned_name = clean_optional(vendor_name)
    cleaned_gstin = clean_optional(vendor_gstin)
    cleaned_pan = clean_optional(vendor_pan)

    normalized_name = normalize_vendor_name(cleaned_name)
    normalized_gstin = normalize_gstin(cleaned_gstin)
    normalized_pan = normalize_pan(cleaned_pan)

    gstin_matches = [
        vendor
        for vendor in vendors
        if normalized_gstin is not None
        and vendor.gstin is not None
        and vendor.gstin.strip().upper() == normalized_gstin
    ]
    pan_matches = [
        vendor
        for vendor in vendors
        if normalized_pan is not None
        and vendor.pan is not None
        and vendor.pan.strip().upper() == normalized_pan
    ]
    name_matches = [
        vendor
        for vendor in vendors
        if normalized_name and normalize_vendor_name(vendor.name) == normalized_name
    ]

    # Duplicate identifier rows are ambiguous, not an opportunity to pick one.
    if len(gstin_matches) > 1:
        return _ambiguous(
            MATCH_REASON_MULTIPLE_GSTIN, [*gstin_matches, *pan_matches, *name_matches]
        )
    if len(pan_matches) > 1:
        return _ambiguous(
            MATCH_REASON_MULTIPLE_PAN, [*gstin_matches, *pan_matches, *name_matches]
        )

    identifier_vendor: Vendor | None = None
    identifier_method: VendorMatchMethod | None = None
    identifier_reason: str | None = None

    if gstin_matches:
        identifier_vendor = gstin_matches[0]
        identifier_method = VendorMatchMethod.EXACT_GSTIN
        identifier_reason = MATCH_REASON_EXACT_GSTIN
    elif pan_matches:
        identifier_vendor = pan_matches[0]
        identifier_method = VendorMatchMethod.EXACT_PAN
        identifier_reason = MATCH_REASON_EXACT_PAN

    if (
        gstin_matches
        and pan_matches
        and gstin_matches[0].id != pan_matches[0].id
    ):
        return _ambiguous(
            MATCH_REASON_IDENTIFIER_CONFLICT,
            [*gstin_matches, *pan_matches, *name_matches],
        )

    if identifier_vendor is not None:
        # A strong identifier matched. The name may differ (for example a
        # shortened trade name), but it must not point at a different vendor.
        if len(name_matches) > 1:
            return _ambiguous(
                MATCH_REASON_MULTIPLE_NAME,
                [identifier_vendor, *name_matches],
            )
        if name_matches and name_matches[0].id != identifier_vendor.id:
            return _ambiguous(
                MATCH_REASON_IDENTIFIER_NAME_CONFLICT,
                [identifier_vendor, *name_matches],
            )
        assert identifier_method is not None and identifier_reason is not None
        return _matched(
            identifier_vendor,
            method=identifier_method,
            reason=identifier_reason,
        )

    if len(name_matches) == 1:
        vendor = name_matches[0]
        trusted = _has_confirmed_name(db, company_id, vendor.id, normalized_name)
        return _matched(
            vendor,
            method=VendorMatchMethod.NORMALIZED_NAME,
            reason=MATCH_REASON_HISTORICAL_NAME if trusted else MATCH_REASON_NORMALIZED_NAME,
            requires_confirmation=not trusted,
        )
    if len(name_matches) > 1:
        return _ambiguous(MATCH_REASON_MULTIPLE_NAME, name_matches)

    if not normalized_name:
        # No name, and neither identifier matched. Distinguish "nothing to go
        # on" from "the printed values were not valid identifiers".
        invalid_identifier = (
            cleaned_gstin is not None and normalized_gstin is None
        ) or (cleaned_pan is not None and normalized_pan is None)
        reason = (
            MATCH_REASON_INVALID_IDENTIFIERS
            if invalid_identifier
            else MATCH_REASON_NO_VENDOR_INFORMATION
            if cleaned_gstin is None and cleaned_pan is None
            else MATCH_REASON_NO_MATCH
        )
        return VendorMatchResult(
            status=VendorMatchStatus.NO_MATCH,
            reason=reason,
        )

    return VendorMatchResult(
        status=VendorMatchStatus.NO_MATCH,
        reason=MATCH_REASON_NO_MATCH,
    )


__all__ = [
    "MATCH_REASON_EXACT_GSTIN",
    "MATCH_REASON_EXACT_PAN",
    "MATCH_REASON_HISTORICAL_NAME",
    "MATCH_REASON_IDENTIFIER_CONFLICT",
    "MATCH_REASON_IDENTIFIER_NAME_CONFLICT",
    "MATCH_REASON_INVALID_IDENTIFIERS",
    "MATCH_REASON_MULTIPLE_GSTIN",
    "MATCH_REASON_MULTIPLE_NAME",
    "MATCH_REASON_MULTIPLE_PAN",
    "MATCH_REASON_NORMALIZED_NAME",
    "MATCH_REASON_NO_MATCH",
    "MATCH_REASON_NO_VENDOR_INFORMATION",
    "VendorCandidate",
    "VendorMatchMethod",
    "VendorMatchResult",
    "VendorMatchStatus",
    "match_vendor",
    "normalize_gstin",
    "normalize_pan",
    "normalize_vendor_name",
]
