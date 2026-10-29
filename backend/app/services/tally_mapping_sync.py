"""Learn ledger mappings from TallyPrime's own historical vouchers.

PAL's suggestion engine resolves an expense ledger from approved mappings or
approved voucher history. A company moving onto PAL has neither, even though
Tally already holds years of purchase vouchers stating exactly which expense
head each party is booked to. This service reads those vouchers and records the
pairings as `AccountingMapping` rows, so the existing deterministic chain
answers from the company's real history instead of reporting
`NO_HISTORICAL_MAPPING`.

What makes this evidence and not a guess:

* only Tally's own posted vouchers are read, never PAL drafts;
* a voucher contributes only when its party line and exactly one non-tax
  expense line can both be resolved in PAL's synced ledger master; and
* a party whose history is split across heads with no clear majority is
  skipped, so an ambiguous party stays a reviewer's decision.

The read is read-only. PAL never writes to TallyPrime.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.integrations.tally import TallyError, TallyService, get_tally_service
from app.models.accounting_mapping import (
    TALLY_HISTORY_CONTEXT,
    AccountingMapping,
    AccountingMappingMatchType,
    AccountingMappingSourceType,
)
from app.models.ledger import Ledger
from app.models.vendor import Vendor
from app.services.company import get_company
from app.services.ledger import is_tax_or_adjustment_ledger

#: Groups holding party ledgers, by voucher direction.
_CREDITOR_GROUPS = {"sundry creditors"}
_DEBTOR_GROUPS = {"sundry debtors"}

#: Groups that are never an expense/income head even when debited.
_NON_EXPENSE_GROUPS = (
    _CREDITOR_GROUPS | _DEBTOR_GROUPS | {"bank accounts", "cash-in-hand", "cash in hand"}
)

#: Tax, withholding and rounding lines are never the accounting head. The
#: predicate lives in `app.services.ledger` so the AI picker applies the same
#: rule when it narrows its candidates.
_is_tax_or_adjustment = is_tax_or_adjustment_ledger

#: A party must use one head in more than this share of its vouchers before the
#: mapping is recorded. Below it the party genuinely spans several heads.
_MIN_MAJORITY = 0.6

#: Fewest vouchers a party needs before its history counts as evidence.
_MIN_VOUCHERS = 1

#: Stamped on `transaction_context` so a later run can tell its own rows apart
#: from a mapping a person confirmed. Only rows carrying this are rewritten.
_TALLY_PROVENANCE = TALLY_HISTORY_CONTEXT


@dataclass(frozen=True)
class TallyMappingSyncResult:
    """Outcome of one mapping-learning run."""

    success: bool
    company_id: uuid.UUID
    tally_company_name: str
    vouchers_fetched: int
    vouchers_used: int
    parties_seen: int
    mappings_created: int
    mappings_updated: int
    mappings_skipped: int
    mappings_preserved: int
    error_message: str | None


def _group_of(ledger: Ledger) -> str:
    return (ledger.parent_group or "").strip().lower()


def _is_purchase(voucher_type: str | None) -> bool:
    return "purchase" in (voucher_type or "").strip().lower()


def _is_sales(voucher_type: str | None) -> bool:
    lowered = (voucher_type or "").strip().lower()
    return "sales" in lowered and "return" not in lowered


def _ledger_index(db: Session, company_id: uuid.UUID) -> dict[str, Ledger]:
    ledgers = db.scalars(
        select(Ledger).where(
            Ledger.company_id == company_id, Ledger.is_active.is_(True)
        )
    ).all()
    return {ledger.name.strip().lower(): ledger for ledger in ledgers}


def _party_and_head(
    voucher, index: dict[str, Ledger], *, is_sales: bool
) -> tuple[Ledger, Ledger] | None:
    """Return (party ledger, expense/income ledger) for one Tally voucher.

    On a purchase the party is credited and the expense debited; a sales
    voucher is the mirror image. Anything ambiguous returns None.
    """
    party_groups = _DEBTOR_GROUPS if is_sales else _CREDITOR_GROUPS
    party: Ledger | None = None
    heads: list[Ledger] = []

    for entry in voucher.entries:
        ledger = index.get((entry.ledger_name or "").strip().lower())
        if ledger is None:
            continue
        group = _group_of(ledger)
        # is_deemed_positive marks the ledger Tally debited.
        debited = entry.is_deemed_positive
        if group in party_groups and debited is is_sales:
            if party is not None and party.id != ledger.id:
                return None
            party = ledger
        elif (
            group not in _NON_EXPENSE_GROUPS
            and not _is_tax_or_adjustment(ledger)
            and debited is not is_sales
        ):
            heads.append(ledger)

    if party is None:
        return None
    distinct = {ledger.id: ledger for ledger in heads}
    if len(distinct) != 1:
        return None
    return party, next(iter(distinct.values()))


def _vendor_for_party(
    db: Session, company_id: uuid.UUID, party_name: str
) -> Vendor | None:
    """Find the PAL vendor whose creditor ledger or name is this Tally party."""
    lowered = party_name.strip().lower()
    vendors = db.scalars(select(Vendor).where(Vendor.company_id == company_id)).all()
    for vendor in vendors:
        if (vendor.creditor_ledger or "").strip().lower() == lowered:
            return vendor
    for vendor in vendors:
        if vendor.name.strip().lower() == lowered:
            return vendor
    return None


def sync_tally_accounting_mappings(
    db: Session,
    company_id: uuid.UUID,
    tally_company_name: str,
    *,
    actor: str = "local-user",
    service: TallyService | None = None,
) -> TallyMappingSyncResult:
    """Record ledger mappings learned from Tally's historical vouchers.

    Requires the ledger master to be synced first: a voucher whose ledgers are
    not in PAL's master cannot contribute. Raises CompanyNotFoundError if the
    company does not exist.
    """
    get_company(db, company_id)
    tally_company_name = tally_company_name.strip()
    owned_service = service is None
    svc: TallyService = service or get_tally_service()

    try:
        result = svc.get_vouchers(tally_company_name)
    except TallyError as exc:
        return TallyMappingSyncResult(
            success=False,
            company_id=company_id,
            tally_company_name=tally_company_name,
            vouchers_fetched=0,
            vouchers_used=0,
            parties_seen=0,
            mappings_created=0,
            mappings_updated=0,
            mappings_skipped=0,
            mappings_preserved=0,
            error_message=str(exc),
        )
    finally:
        if owned_service:
            svc.close()

    index = _ledger_index(db, company_id)
    # (party ledger id, voucher kind) -> Counter of head ledger ids
    observed: dict[tuple[uuid.UUID, str], Counter] = defaultdict(Counter)
    party_names: dict[uuid.UUID, str] = {}
    used = 0

    for voucher in result.vouchers:
        is_sales = _is_sales(voucher.voucher_type)
        if not is_sales and not _is_purchase(voucher.voucher_type):
            continue
        pair = _party_and_head(voucher, index, is_sales=is_sales)
        if pair is None:
            continue
        party, head = pair
        observed[(party.id, "SALES" if is_sales else "PURCHASE")][head.id] += 1
        party_names[party.id] = party.name
        used += 1

    created = updated = skipped = preserved = 0
    now = datetime.now(tz=timezone.utc)

    for (party_id, kind), counts in observed.items():
        total = sum(counts.values())
        head_id, hits = counts.most_common(1)[0]
        if total < _MIN_VOUCHERS or hits / total < _MIN_MAJORITY:
            skipped += 1
            continue

        party_name = party_names[party_id]
        vendor = _vendor_for_party(db, company_id, party_name)
        head = db.get(Ledger, head_id)
        if head is None:
            skipped += 1
            continue

        reason = (
            f"Tally history: {hits} of {total} {kind.lower()} voucher(s) for "
            f"'{party_name}' used '{head.name}'."
        )
        # Match on either identity a mapping may carry: a manual row created
        # through the API often has only vendor_id, this service's own rows
        # carry the party name. Missing one would add a second active mapping
        # for the same party and make the suggestion AMBIGUOUS.
        identity = [AccountingMapping.counterparty_name == party_name]
        if vendor is not None:
            identity.append(AccountingMapping.vendor_id == vendor.id)
        existing = db.scalar(
            select(AccountingMapping).where(
                AccountingMapping.company_id == company_id,
                AccountingMapping.source_type
                == AccountingMappingSourceType.INVOICE,
                AccountingMapping.voucher_type == kind,
                or_(*identity),
            )
        )
        if existing is not None and existing.transaction_context != _TALLY_PROVENANCE:
            # A person confirmed this one. Tally history never overrides it.
            preserved += 1
            continue
        if existing is None:
            db.add(
                AccountingMapping(
                    company_id=company_id,
                    source_type=AccountingMappingSourceType.INVOICE,
                    match_type=(
                        AccountingMappingMatchType.VENDOR
                        if vendor is not None
                        else AccountingMappingMatchType.COUNTERPARTY
                    ),
                    vendor_id=vendor.id if vendor is not None else None,
                    counterparty_name=party_name,
                    transaction_context=_TALLY_PROVENANCE,
                    voucher_type=kind,
                    ledger_id=head.id,
                    reason=reason,
                    approved_by=actor,
                    approved_at=now,
                    is_active=True,
                )
            )
            created += 1
        elif existing.ledger_id != head.id or existing.reason != reason:
            existing.ledger_id = head.id
            existing.reason = reason
            existing.vendor_id = vendor.id if vendor is not None else None
            existing.match_type = (
                AccountingMappingMatchType.VENDOR
                if vendor is not None
                else AccountingMappingMatchType.COUNTERPARTY
            )
            existing.is_active = True
            existing.approved_by = actor
            existing.approved_at = now
            updated += 1

    db.commit()
    return TallyMappingSyncResult(
        success=True,
        company_id=company_id,
        tally_company_name=tally_company_name,
        vouchers_fetched=len(result.vouchers),
        vouchers_used=used,
        parties_seen=len(observed),
        mappings_created=created,
        mappings_updated=updated,
        mappings_skipped=skipped,
        mappings_preserved=preserved,
        error_message=None,
    )


__all__ = ["TallyMappingSyncResult", "sync_tally_accounting_mappings"]
