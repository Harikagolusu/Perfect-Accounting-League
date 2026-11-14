"""Ledger Master business logic.

Every read and write is company-scoped. A ledger id from another company is
indistinguishable from a missing one, exactly as in the Vendor/Document
services.
"""

from __future__ import annotations

import re
import uuid
from difflib import SequenceMatcher

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.integrations.tally import TallyError, TallyService, get_tally_service
from app.models.ledger import Ledger, LedgerSource
from app.models.voucher import AccountingEntityType
from app.schemas.ledger import LedgerCreate, LedgerUpdate
from app.services.accounting_audit import record_event
from app.services.company import get_company

#: Group a manually-added ledger lands in when the user picked none. Most
#: hand-added ledgers are parties; Sundry Creditors always exists in Tally.
#: shortcut: assume creditor when unspecified; revisit if users add expense
#: ledgers without choosing a parent group.
_DEFAULT_LEDGER_PARENT = "Sundry Creditors"


class LedgerNotFoundError(Exception):
    """Raised when a ledger id does not resolve within the company."""


class LedgerConflictError(Exception):
    """Raised for a duplicate or otherwise conflicting ledger name/code."""


class LedgerSyncError(Exception):
    """Raised when TallyPrime did not accept a ledger create (offline/reject)."""


def sync_ledger_to_tally(
    company,
    *,
    name: str,
    parent: str,
    gstin: str | None = None,
    service: TallyService | None = None,
) -> None:
    """Create a ledger master in TallyPrime, raising if Tally does not accept it.

    Block semantics: the caller commits to PAL only after this returns cleanly.
    The Tally company name follows the same precedence as voucher posting.
    """
    target_company = (
        getattr(company, "legal_name", None) or getattr(company, "name", None) or ""
    ).strip()
    owned = service is None
    svc = service or get_tally_service()
    try:
        result = svc.create_ledger(
            name=name, parent=parent, company=target_company or None, gstin=gstin
        )
    except TallyError as exc:
        raise LedgerSyncError(
            f"TallyPrime did not accept the ledger '{name}': {exc}"
        ) from exc
    finally:
        if owned:
            svc.close()
    if not result.accepted:
        raise LedgerSyncError(
            f"TallyPrime rejected the ledger '{name}': "
            f"{result.message or 'see TallyPrime for details'}"
        )


def list_ledgers(
    db: Session,
    company_id: uuid.UUID,
    *,
    include_inactive: bool = False,
    search: str | None = None,
) -> list[Ledger]:
    query = select(Ledger).where(Ledger.company_id == company_id)
    if not include_inactive:
        query = query.where(Ledger.is_active.is_(True))
    if search:
        term = f"%{search.strip()}%"
        query = query.where(
            Ledger.name.ilike(term) | Ledger.code.ilike(term)
        )
    query = query.order_by(Ledger.name, Ledger.created_at)
    return list(db.scalars(query).all())


def get_ledger(db: Session, company_id: uuid.UUID, ledger_id: uuid.UUID) -> Ledger:
    ledger = db.scalar(
        select(Ledger).where(
            Ledger.id == ledger_id, Ledger.company_id == company_id
        )
    )
    if ledger is None:
        raise LedgerNotFoundError(f"{company_id}/{ledger_id}")
    return ledger


def find_ledger_by_name(
    db: Session, company_id: uuid.UUID, name: str | None, *, active_only: bool = True
) -> Ledger | None:
    """Resolve a ledger by its company-scoped, case-insensitive name."""
    if not name or not name.strip():
        return None
    query = select(Ledger).where(
        Ledger.company_id == company_id,
        func.lower(Ledger.name) == name.strip().lower(),
    )
    if active_only:
        query = query.where(Ledger.is_active.is_(True))
    return db.scalar(query.order_by(Ledger.created_at).limit(1))


#: Tax and withholding ledgers, matched on the ledger's own name or its group.
#: A fixed group list is not enough: real charts of accounts file GST under
#: names like "GST_MP" rather than "Duties & Taxes". Tokens are word-matched so
#: an expense ledger such as "Taxi Fare" is not caught.
_TAX_TOKEN_RE = re.compile(
    r"\b(?:[csiu]?gst|cess|tds|tcs|duties|taxes?|vat)\b", re.IGNORECASE
)

#: Rounding adjustments are never the accounting head of an invoice.
_ADJUSTMENT_RE = re.compile(r"\bround(?:ed)?[\s-]*off\b", re.IGNORECASE)

#: Group tokens that mark a ledger as something other than an expense/income
#: head: a party, a bank, or a balance-sheet account.
_NON_HEAD_GROUP_TOKENS = (
    "creditor",
    "debtor",
    "bank",
    "cash",
    "capital",
    "loan",
    "provision",
    "deposit",
    "asset",
    "liabilit",
    "payable",
    "advance",
    "suspense",
    "share",
    "branch",
    "stock",
)


def _tokenised(text: str) -> str:
    """Separator-insensitive text, so "GST_MP" word-matches "gst"."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower())


def is_tax_or_adjustment_ledger(ledger: Ledger) -> bool:
    """True for a GST/TDS/cess ledger or a rounding adjustment."""
    name = _tokenised(ledger.name)
    group = _tokenised(ledger.parent_group or "")
    if _ADJUSTMENT_RE.search(name):
        return True
    return bool(_TAX_TOKEN_RE.search(name) or _TAX_TOKEN_RE.search(group))


def is_posting_head_candidate(ledger: Ledger) -> bool:
    """True when a ledger could be the expense/income head of an invoice.

    Used to narrow the choices offered for a ledger decision. A party, bank or
    balance-sheet ledger is never the head, so excluding them keeps the choice
    both smaller and harder to get wrong. Anything unrecognised is kept: this
    narrows the field, it does not decide.
    """
    if is_tax_or_adjustment_ledger(ledger):
        return False
    group = _tokenised(ledger.parent_group or "")
    return not any(token in group for token in _NON_HEAD_GROUP_TOKENS)


#: Company-form suffixes that differ only in abbreviation between an invoice's
#: printed party name and the Tally ledger name for the same party.
_NAME_SUFFIX_FORMS = (
    ("private limited", "pvtltd"),
    ("pvt limited", "pvtltd"),
    ("pvt ltd", "pvtltd"),
    ("public limited", "ltd"),
    ("limited", "ltd"),
    ("llp", "llp"),
    ("and associates", "associates"),
    ("& associates", "associates"),
)

#: Minimum similarity for a fuzzy ledger name match, and the margin the winner
#: must beat the runner-up by. Both are deliberately strict: a wrong creditor
#: ledger posts an invoice against the wrong party.
_FUZZY_MIN_RATIO = 0.88

#: The canonical legal suffix at the end of a comparison key.
_LEGAL_SUFFIX_RE = re.compile(r"(pvtltd|ltd|llp)$")
_FUZZY_MIN_MARGIN = 0.04


def _comparison_key(name: str) -> str:
    """Collapse a party name so abbreviation and punctuation stop mattering."""
    lowered = name.casefold()
    for printed, canonical in _NAME_SUFFIX_FORMS:
        lowered = lowered.replace(printed, canonical)
    return re.sub(r"[^a-z0-9]+", "", lowered)


def match_ledger_by_name(
    db: Session,
    company_id: uuid.UUID,
    name: str | None,
    *,
    parent_group: str | None = None,
) -> Ledger | None:
    """Resolve a name to an active ledger, tolerating abbreviation and typos.

    Exact match first, then a punctuation/suffix-insensitive key, then a fuzzy
    comparison. `parent_group` confines the fuzzy pass to one Tally group so a
    party name can never match an expense or tax ledger. Returns None when no
    candidate is clearly best, leaving the choice to a reviewer.
    """
    if not name or not name.strip():
        return None

    exact = find_ledger_by_name(db, company_id, name)
    if exact is not None:
        return exact

    query = select(Ledger).where(
        Ledger.company_id == company_id, Ledger.is_active.is_(True)
    )
    if parent_group:
        query = query.where(func.lower(Ledger.parent_group) == parent_group.lower())
    candidates = list(db.scalars(query).all())
    if not candidates:
        return None

    target = _comparison_key(name)
    if not target:
        return None

    keyed = [(ledger, _comparison_key(ledger.name)) for ledger in candidates]
    identical = [ledger for ledger, key in keyed if key == target]
    if len(identical) == 1:
        return identical[0]
    if identical:
        return None

    # Invoices often drop the legal suffix Tally keeps ("Kalash Travels" against
    # "KALASH TRAVELS PVT LTD"). Equal once it is removed, and unique, is a match.
    bare_target = _LEGAL_SUFFIX_RE.sub("", target)
    bare = [ledger for ledger, key in keyed if bare_target and _LEGAL_SUFFIX_RE.sub("", key) == bare_target]
    if len(bare) == 1:
        return bare[0]

    scored = sorted(
        (
            (SequenceMatcher(None, target, key).ratio(), ledger)
            for ledger, key in keyed
            if key
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not scored or scored[0][0] < _FUZZY_MIN_RATIO:
        return None
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if scored[0][0] - runner_up < _FUZZY_MIN_MARGIN:
        return None
    return scored[0][1]


def _assert_unique_name(
    db: Session,
    company_id: uuid.UUID,
    name: str,
    *,
    exclude_id: uuid.UUID | None = None,
) -> None:
    query = select(Ledger.id).where(
        Ledger.company_id == company_id,
        func.lower(Ledger.name) == name.strip().lower(),
    )
    if exclude_id is not None:
        query = query.where(Ledger.id != exclude_id)
    if db.scalar(query.limit(1)) is not None:
        raise LedgerConflictError("A ledger with this name already exists.")


def create_ledger(
    db: Session,
    company_id: uuid.UUID,
    data: LedgerCreate,
    *,
    actor: str = "local-user",
    service: TallyService | None = None,
) -> Ledger:
    company = get_company(db, company_id)
    _assert_unique_name(db, company_id, data.name)
    # Block semantics: create in Tally first; only persist in PAL if Tally
    # accepted it, so the two never diverge (raises LedgerSyncError otherwise).
    sync_ledger_to_tally(
        company,
        name=data.name,
        parent=data.parent_group or _DEFAULT_LEDGER_PARENT,
        service=service,
    )
    ledger = Ledger(company_id=company_id, **data.model_dump())
    db.add(ledger)
    db.flush()
    record_event(
        db,
        company_id=company_id,
        entity_type=AccountingEntityType.LEDGER,
        entity_id=ledger.id,
        event_type="LEDGER_CREATED",
        actor=actor,
        new_status="ACTIVE",
        metadata={
            "name": ledger.name,
            "code": ledger.code,
            "ledger_type": ledger.ledger_type,
            "source": ledger.source.value,
        },
    )
    db.commit()
    db.refresh(ledger)
    return ledger


def update_ledger(
    db: Session, ledger: Ledger, data: LedgerUpdate, *, actor: str = "local-user"
) -> Ledger:
    payload = data.model_dump(exclude_unset=True)
    if "name" in payload and payload["name"] is not None:
        _assert_unique_name(
            db, ledger.company_id, payload["name"], exclude_id=ledger.id
        )

    for field, value in payload.items():
        old = getattr(ledger, field)
        if old != value:
            record_event(
                db,
                company_id=ledger.company_id,
                entity_type=AccountingEntityType.LEDGER,
                entity_id=ledger.id,
                event_type="LEDGER_UPDATED",
                actor=actor,
                field=field,
                old_value=old,
                new_value=value,
                previous_status="ACTIVE" if ledger.is_active else "INACTIVE",
                new_status=(
                    "ACTIVE"
                    if (value if field == "is_active" else ledger.is_active)
                    else "INACTIVE"
                ),
            )
            setattr(ledger, field, value)

    db.commit()
    db.refresh(ledger)
    return ledger


def deactivate_ledger(
    db: Session, ledger: Ledger, *, actor: str = "local-user"
) -> Ledger:
    if ledger.is_active:
        ledger.is_active = False
        record_event(
            db,
            company_id=ledger.company_id,
            entity_type=AccountingEntityType.LEDGER,
            entity_id=ledger.id,
            event_type="LEDGER_DEACTIVATED",
            actor=actor,
            previous_status="ACTIVE",
            new_status="INACTIVE",
            reason="Ledger deactivated; historical vouchers keep their name snapshot.",
        )
        db.commit()
        db.refresh(ledger)
    return ledger


__all__ = [
    "LedgerConflictError",
    "LedgerNotFoundError",
    "create_ledger",
    "deactivate_ledger",
    "find_ledger_by_name",
    "get_ledger",
    "is_posting_head_candidate",
    "is_tax_or_adjustment_ledger",
    "list_ledgers",
    "match_ledger_by_name",
    "update_ledger",
]
