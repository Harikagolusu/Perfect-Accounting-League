"""Tally→PAL master data synchronisation service.

Fetches companies and ledgers from a live TallyPrime instance and upserts them
into PAL's ledger master for one company. Existing PAL accounting data (vouchers,
mappings, invoices) is never touched. MANUAL ledgers are never deleted.

Duplicate prevention: the existing unique constraint uq_ledgers_company_name on
(company_id, name) is the authoritative guard. The service does a case-insensitive
lookup before creating, so repeated syncs produce no duplicates.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.integrations.tally import TallyError, TallyService, get_tally_service
from app.models.bank import (
    BankAccount,
    BankStatement,
    BankStatementStatus,
    BankTransaction,
)
from app.models.company import Company, CompanyStatus
from app.models.ledger import Ledger, LedgerSource
from app.models.tally_sync import TallySyncLog
from app.models.vendor import Vendor
from app.services.bank_validation import derive_transaction_type
from app.services.company import CompanyNotFoundError, get_company
from app.services.ledger import find_ledger_by_name, match_ledger_by_name

ZERO = Decimal("0")


class TallySyncError(Exception):
    """Tally connectivity or parsing failure during sync."""


@dataclass(frozen=True)
class TallyCompanySyncResult:
    """Outcome of one Tally company-master sync run."""

    success: bool
    companies_fetched: int
    companies_created: int
    companies_updated: int
    companies_unchanged: int
    error_message: str | None = None


def sync_tally_companies(
    db: Session,
    *,
    service: TallyService | None = None,
) -> TallyCompanySyncResult:
    """Upsert TallyPrime companies into PAL's company master.

    Tally is the source for company identity: a company loaded in Tally is
    created in PAL with the same name. Existing PAL companies are matched
    case-insensitively and are never hard-deleted. If a company was archived
    locally (``INACTIVE``) but is still present in Tally, the sync restores it
    to ``ACTIVE``. A blank Tally company list is a successful no-op; a Tally
    failure is returned as ``success=False`` so the UI can show the error
    without treating it as a server crash.
    """
    fetched = created = updated = unchanged = 0
    error_message: str | None = None
    owned_service = service is None
    svc: TallyService | None = service

    try:
        if svc is None:
            svc = get_tally_service()
        tally_companies = svc.get_companies()
        fetched = len(tally_companies)

        for tally_company in tally_companies:
            name = (tally_company.name or "").strip()
            if not name:
                continue

            existing = db.scalar(
                select(Company).where(func.lower(Company.name) == name.lower())
            )
            if existing is None:
                db.add(
                    Company(
                        name=name,
                        legal_name=name,
                        status=CompanyStatus.ACTIVE,
                    )
                )
                created += 1
                continue

            changed = False
            if existing.name != name:
                existing.name = name
                changed = True
            if not (existing.legal_name or "").strip():
                existing.legal_name = name
                changed = True
            if existing.status == CompanyStatus.INACTIVE:
                existing.status = CompanyStatus.ACTIVE
                changed = True

            if changed:
                updated += 1
            else:
                unchanged += 1

        db.commit()
    except TallyError as exc:
        db.rollback()
        error_message = str(exc)
    except Exception:
        db.rollback()
        raise
    finally:
        if owned_service and svc is not None:
            svc.close()

    return TallyCompanySyncResult(
        success=error_message is None,
        companies_fetched=fetched,
        companies_created=created,
        companies_updated=updated,
        companies_unchanged=unchanged,
        error_message=error_message,
    )


def _upsert_ledgers(
    db: Session,
    company_id: uuid.UUID,
    tally_ledgers,
) -> tuple[int, int, int]:
    """Upsert Tally ledgers into PAL. Returns (created, updated, unchanged)."""
    created = updated = unchanged = 0

    for tl in tally_ledgers:
        name = (tl.name or "").strip()
        if not name:
            continue

        existing = db.scalar(
            select(Ledger).where(
                Ledger.company_id == company_id,
                func.lower(Ledger.name) == name.lower(),
            )
        )

        if existing is None:
            ledger = Ledger(
                company_id=company_id,
                name=name,
                parent_group=tl.parent,
                source=LedgerSource.TALLY,
                external_tally_id=name,
                is_active=True,
            )
            db.add(ledger)
            created += 1
        else:
            new_parent = tl.parent or None
            if existing.parent_group != new_parent:
                existing.parent_group = new_parent
                updated += 1
            else:
                unchanged += 1

    return created, updated, unchanged


#: Preferred Tally names for each company-level ledger field, best first. Only
#: an exact (case-insensitive) hit counts, so an unusual chart of accounts
#: leaves the field unset for a person to pick rather than guessing between
#: lookalikes such as "CGST Input" and "Output CGST_KA".
_COMPANY_LEDGER_PREFERENCES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cgst_ledger", ("CGST Input", "Input CGST", "CGST")),
    ("sgst_ledger", ("SGST Input", "Input SGST", "SGST")),
    ("igst_ledger", ("IGST Input", "Input IGST", "IGST")),
    ("cess_ledger", ("Cess Input", "Input Cess", "Cess")),
    ("round_off_ledger", ("Round Off", "Rounded Off", "Round-off")),
    ("sales_ledger", ("Sales", "Sales Accounts", "Sales A/c")),
)

#: Tally group holding party/creditor ledgers.
_CREDITOR_GROUP = "Sundry Creditors"


def _automap_company_ledgers(db: Session, company_id: uuid.UUID) -> int:
    """Fill unset company ledger fields from the synced ledger master."""
    company = get_company(db, company_id)
    filled = 0
    for field_name, preferences in _COMPANY_LEDGER_PREFERENCES:
        if (getattr(company, field_name) or "").strip():
            continue
        for preference in preferences:
            ledger = find_ledger_by_name(db, company_id, preference)
            if ledger is not None:
                setattr(company, field_name, ledger.name)
                filled += 1
                break
    return filled


def _automap_vendor_ledgers(db: Session, company_id: uuid.UUID) -> int:
    """Point each vendor's creditor ledger at a real Tally ledger.

    A vendor auto-created from an invoice carries the printed party name as its
    creditor ledger, which rarely matches Tally's own spelling ("RADHIKA DEVCON
    PRIVATE LIMITED" against "RADHIKA DWCON PVT LTD"). Only names that do not
    already resolve are touched, so a reviewer's correction is never undone.
    """
    vendors = list(
        db.scalars(select(Vendor).where(Vendor.company_id == company_id)).all()
    )
    remapped = 0
    for vendor in vendors:
        current = (vendor.creditor_ledger or "").strip()
        if current and find_ledger_by_name(db, company_id, current) is not None:
            continue
        ledger = match_ledger_by_name(
            db,
            company_id,
            current or vendor.name,
            parent_group=_CREDITOR_GROUP,
        )
        if ledger is not None and ledger.name != current:
            vendor.creditor_ledger = ledger.name
            remapped += 1
    return remapped


def sync_tally_ledgers(
    db: Session,
    company_id: uuid.UUID,
    tally_company_name: str,
    *,
    actor: str = "local-user",
    service: TallyService | None = None,
) -> TallySyncLog:
    """Fetch ledgers from TallyPrime and upsert into the PAL ledger master.

    Once the master is up to date, unset company tax/sales ledgers and
    unresolved vendor creditor ledgers are pointed at the synced ledgers so
    vouchers stop reporting MISSING_LEDGER for ledgers Tally already has. Only
    blank or unresolvable values are filled; a reviewer's own choice stands.

    Returns a TallySyncLog record. On Tally failure the log records the error
    and no ledger rows are committed.

    Raises CompanyNotFoundError if company_id does not exist in PAL.
    """
    get_company(db, company_id)

    tally_company_name = tally_company_name.strip()
    owned_service = service is None
    svc: TallyService = service or get_tally_service()

    error_message: str | None = None
    fetched = created = updated = unchanged = 0

    try:
        result = svc.get_ledgers(tally_company_name)
        fetched = len(result.ledgers)
        created, updated, unchanged = _upsert_ledgers(db, company_id, result.ledgers)
        # Flush so the auto-map below resolves names against this run's rows.
        db.flush()
        _automap_company_ledgers(db, company_id)
        _automap_vendor_ledgers(db, company_id)
    except TallyError as exc:
        error_message = str(exc)
    finally:
        if owned_service:
            svc.close()

    log = TallySyncLog(
        company_id=company_id,
        tally_company_name=tally_company_name,
        synced_at=datetime.now(tz=timezone.utc),
        ledgers_fetched=fetched,
        ledgers_created=created,
        ledgers_updated=updated,
        ledgers_unchanged=unchanged,
        error_message=error_message,
        triggered_by=actor,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


@dataclass(frozen=True)
class TallyVendorSyncResult:
    """Outcome of one Tally creditor-ledger -> PAL vendor sync run."""

    success: bool
    tally_company_name: str
    vendors_fetched: int
    vendors_created: int
    vendors_updated: int
    vendors_unchanged: int
    error_message: str | None = None


def _upsert_vendors(
    db: Session,
    company_id: uuid.UUID,
    creditor_ledgers,
) -> tuple[int, int, int]:
    """Create a vendor per Tally creditor ledger. Returns (created, updated, unchanged).

    Matching is by vendor name, case-insensitively, so repeated syncs never
    duplicate a vendor. An existing vendor only gets blank fields filled; a
    reviewer's own values are never overwritten.
    """
    created = updated = unchanged = 0

    for ledger in creditor_ledgers:
        name = (ledger.name or "").strip()
        if not name:
            continue

        existing = db.scalar(
            select(Vendor).where(
                Vendor.company_id == company_id,
                func.lower(func.trim(Vendor.name)) == name.lower(),
            )
        )

        if existing is None:
            db.add(
                Vendor(
                    company_id=company_id,
                    name=name,
                    gstin=ledger.gstin or None,
                    pan=ledger.pan or None,
                    creditor_ledger=name,
                    parent_group=ledger.parent or _CREDITOR_GROUP,
                )
            )
            created += 1
            continue

        changed = False
        for attr, value in (
            ("gstin", ledger.gstin),
            ("pan", ledger.pan),
            ("creditor_ledger", name),
            ("parent_group", ledger.parent or _CREDITOR_GROUP),
        ):
            if value and not (getattr(existing, attr) or "").strip():
                setattr(existing, attr, value)
                changed = True
        if changed:
            updated += 1
        else:
            unchanged += 1

    return created, updated, unchanged


def sync_tally_vendors(
    db: Session,
    company_id: uuid.UUID,
    tally_company_name: str,
    *,
    service: TallyService | None = None,
) -> TallyVendorSyncResult:
    """Import Tally ledgers under Sundry Creditors as PAL vendors.

    Raises CompanyNotFoundError when company_id is not in PAL.
    """
    get_company(db, company_id)

    tally_company_name = tally_company_name.strip()
    owned_service = service is None
    svc: TallyService = service or get_tally_service()

    fetched = created = updated = unchanged = 0
    error_message: str | None = None

    try:
        result = svc.get_ledgers(tally_company_name)
        creditors = [
            ledger
            for ledger in result.ledgers
            if (ledger.parent or "").strip().lower() == _CREDITOR_GROUP.lower()
        ]
        fetched = len(creditors)
        created, updated, unchanged = _upsert_vendors(db, company_id, creditors)
        db.commit()
    except TallyError as exc:
        db.rollback()
        error_message = str(exc)
    except Exception:
        db.rollback()
        raise
    finally:
        if owned_service:
            svc.close()

    return TallyVendorSyncResult(
        success=error_message is None,
        tally_company_name=tally_company_name,
        vendors_fetched=fetched,
        vendors_created=created,
        vendors_updated=updated,
        vendors_unchanged=unchanged,
        error_message=error_message,
    )


def get_latest_sync(
    db: Session,
    company_id: uuid.UUID,
) -> TallySyncLog | None:
    """Return the most recent sync log for this company, or None."""
    return db.scalar(
        select(TallySyncLog)
        .where(TallySyncLog.company_id == company_id)
        .order_by(TallySyncLog.synced_at.desc())
        .limit(1)
    )


# Tally groups that represent bank accounts.
_BANK_GROUPS = {"bank accounts", "bank od account", "bank od accounts"}

#: Groups where a Tally debit increases the bank balance. For an overdraft or
#: credit-card ledger the direction is the other way round.
_DEPOSIT_GROUPS = {"bank accounts"}

# Placeholder stored when Tally has no account number for an imported account.
_TALLY_ACCOUNT_NUMBER = "TALLY-IMPORT"

#: Marks a statement PAL created from a Tally voucher read, so the next sync
#: finds the same statement instead of appending to an uploaded one.
_TALLY_STATEMENT_SOURCE = "TALLY"


@dataclass(frozen=True)
class TallyBankSyncResult:
    """Outcome of one Tally bank-account sync run."""

    success: bool
    tally_company_name: str
    accounts_fetched: int
    accounts_created: int
    accounts_updated: int
    accounts_unchanged: int
    error_message: str | None = None


def _bank_ledgers(ledgers) -> list:
    """Return the ledgers Tally groups as bank accounts."""
    return [
        ledger
        for ledger in ledgers
        if (ledger.parent or "").strip().lower() in _BANK_GROUPS
    ]


def _upsert_bank_accounts(
    db: Session,
    company_id: uuid.UUID,
    bank_ledgers,
) -> tuple[int, int, int]:
    """Upsert Tally bank ledgers into PAL accounts.

    Returns (created, updated, unchanged). Matching is by ledger_name,
    case-insensitively, so repeated syncs never duplicate an account.
    """
    created = updated = unchanged = 0

    for ledger in bank_ledgers:
        name = (ledger.name or "").strip()
        if not name:
            continue

        existing = db.scalar(
            select(BankAccount).where(
                BankAccount.company_id == company_id,
                func.lower(BankAccount.ledger_name) == name.lower(),
            )
        )

        if existing is None:
            db.add(
                BankAccount(
                    company_id=company_id,
                    bank_name=name,
                    account_name=name,
                    account_number=_TALLY_ACCOUNT_NUMBER,
                    ledger_name=name,
                    currency="INR",
                    is_active=True,
                )
            )
            created += 1
            continue

        changed = False
        if not existing.is_active:
            existing.is_active = True
            changed = True
        if existing.bank_name != name:
            existing.bank_name = name
            changed = True
        if changed:
            updated += 1
        else:
            unchanged += 1

    return created, updated, unchanged


def sync_tally_bank_accounts(
    db: Session,
    company_id: uuid.UUID,
    tally_company_name: str,
    *,
    service: TallyService | None = None,
) -> TallyBankSyncResult:
    """Fetch bank-account ledgers from Tally and upsert into PAL bank_accounts.

    Tally ledgers whose parent group is "Bank Accounts" or "Bank OD Account"
    are treated as bank accounts. Matching is by ledger_name (case-insensitive).
    The account_number is set to TALLY-IMPORT as a placeholder; update it with
    the real number after importing.

    Raises CompanyNotFoundError when company_id is not in PAL.
    """
    get_company(db, company_id)

    tally_company_name = tally_company_name.strip()
    owned_service = service is None
    svc: TallyService = service or get_tally_service()

    fetched = created = updated = unchanged = 0
    error_message: str | None = None

    try:
        result = svc.get_ledgers(tally_company_name)
        bank_ledgers = _bank_ledgers(result.ledgers)
        fetched = len(bank_ledgers)
        created, updated, unchanged = _upsert_bank_accounts(
            db, company_id, bank_ledgers
        )
        db.commit()
    except TallyError as exc:
        db.rollback()
        error_message = str(exc)
    except Exception:
        db.rollback()
        raise
    finally:
        if owned_service:
            svc.close()

    return TallyBankSyncResult(
        success=error_message is None,
        tally_company_name=tally_company_name,
        accounts_fetched=fetched,
        accounts_created=created,
        accounts_updated=updated,
        accounts_unchanged=unchanged,
        error_message=error_message,
    )


@dataclass
class _GstTotals:
    """Mutable accumulator for one side of the GST working."""

    taxable: Decimal = ZERO
    igst: Decimal = ZERO
    cgst: Decimal = ZERO
    sgst: Decimal = ZERO


@dataclass(frozen=True)
class TallyGstWorkingResult:
    """Outward tax and inward ITC aggregated from Tally vouchers for a period."""

    success: bool
    tally_company_name: str
    from_date: date | None
    to_date: date | None
    outward: _GstTotals
    inward_itc: _GstTotals
    vouchers_fetched: int
    vouchers_used: int
    error_message: str | None = None


@dataclass(frozen=True)
class TallyGstLine:
    """One voucher's contribution to the GST working, for the per-row export.

    ``direction`` is 'outward' (sale/sales return) or 'inward' (purchase/return);
    returns already carry negative amounts, so a row sums straight into a total.
    """

    direction: str
    date: date | None
    voucher_number: str | None
    voucher_type: str | None
    party_name: str | None
    gstin: str | None
    reference: str | None
    taxable: Decimal
    igst: Decimal
    cgst: Decimal
    sgst: Decimal
    total_gst: Decimal
    invoice_value: Decimal


@dataclass(frozen=True)
class TallyHsnRow:
    """One HSN-summary row: outward supplies grouped by HSN code + tax rate."""

    hsn: str
    rate: Decimal
    unit: str
    quantity: Decimal
    taxable: Decimal
    igst: Decimal
    cgst: Decimal
    sgst: Decimal
    #: Distinct stock item names in the group, so each row is identifiable (and
    #: the no-HSN row names the items whose HSN must be set in Tally).
    description: str


@dataclass(frozen=True)
class TallyGstDetailResult:
    """Aggregates plus the per-voucher rows behind them."""

    success: bool
    tally_company_name: str
    from_date: date | None
    to_date: date | None
    outward: _GstTotals
    inward_itc: _GstTotals
    lines: list[TallyGstLine]
    vouchers_fetched: int
    vouchers_used: int
    hsn_rows: list[TallyHsnRow] = field(default_factory=list)
    error_message: str | None = None


def _gst_component(ledger_name: str) -> str | None:
    """Return 'igst' | 'cgst' | 'sgst' for a GST tax ledger, else None.

    UTGST is reported alongside SGST. Cess and other heads are not GST
    components and return None (they fall into the taxable base — a reviewer
    corrects the rare voucher where that is wrong).

    A trading ledger named after the tax ("IGST Sales 18%", "GST Purchase 18%")
    is the taxable value, not a tax head, so it is treated as base. Only the tax
    ledger (e.g. "IGST @ 18 %") is a component.
    """
    name = ledger_name.lower()
    if "sales" in name or "purchase" in name:
        return None
    if "igst" in name:
        return "igst"
    if "cgst" in name:
        return "cgst"
    # "sgts" is a transposed "sgst": the client's chart of accounts has a ledger
    # literally named "SGTS 9 %". Without it that SGST leg is mis-read as taxable
    # value, so CGST and SGST no longer match on intra-state supplies.
    if "sgst" in name or "utgst" in name or "sgts" in name:
        return "sgst"
    return None


# shortcut: match TCS/TDS/round-off ledgers by name; upgrade to the ledger
# master's statutory nature if a sales ledger ever contains one of these words.
_NON_TAXABLE_LEDGER_HINTS = ("tcs", "tds", "round")


def _is_non_taxable_ledger(ledger_name: str) -> bool:
    """True for a ledger outside the GST taxable base: TCS, TDS, rounding.

    These are collected on top of (or adjust) the invoice value but are not the
    value of the supply, so they must not inflate the taxable total."""
    name = ledger_name.lower()
    return any(hint in name for hint in _NON_TAXABLE_LEDGER_HINTS)


#: Tally order/inventory voucher types that are not tax invoices; excluded from
#: every GST working even when they post estimated GST ledgers.
_NON_ACCOUNTING_VOUCHER_TYPES = {
    "sales order", "purchase order", "quotation",
    "delivery note", "receipt note",
    "rejections in", "rejections out", "material in", "material out",
    "stock journal", "physical stock",
}


def _classify_voucher_gst(voucher) -> tuple[str, bool, int] | None:
    """Return (direction, want_debit, sign) for a GST voucher, else None.

    ``direction`` is 'outward' or 'inward'; ``want_debit`` is the ledger side the
    taxable + GST lines sit on; ``sign`` is -1 for a return so it nets out.

    A Credit Note is a sales return and a Debit Note a purchase return: Tally
    posts their GST on the side opposite a plain invoice, so name them explicitly
    and net them out.

    For every other voucher the direction comes from the GST ledger name when it
    marks input/output: a Journal can book a purchase's *input* tax on the credit
    side, which the side alone would misread as a sale. Only when the name is
    unmarked does the side decide (credited GST = output).
    """
    gst_entries = [e for e in voucher.entries if _gst_component(e.ledger_name)]
    if not gst_entries:
        return None

    voucher_type = (voucher.voucher_type or "").strip().lower()
    # Order and inventory-only vouchers carry only estimated GST and are never a
    # tax invoice, so they are excluded from every GST working regardless of
    # whether their entries happen to net to zero. (Client: Sales/Purchase Orders
    # must not appear; in this company every order balances, so a balance test
    # let them all through.)
    if voucher_type in _NON_ACCOUNTING_VOUCHER_TYPES:
        return None
    if voucher_type == "credit note":
        return "outward", True, -1
    if voucher_type == "debit note":
        return "inward", False, -1

    has_debit_gst = any(e.is_deemed_positive for e in gst_entries)
    has_credit_gst = any(not e.is_deemed_positive for e in gst_entries)
    if has_debit_gst and has_credit_gst:
        return None  # GST on both sides (RCM self-invoice) — left for manual review

    want_debit = gst_entries[0].is_deemed_positive
    names = " ".join(e.ledger_name.lower() for e in gst_entries)
    if "input" in names:
        direction = "inward"
    elif "output" in names:
        direction = "outward"
    else:
        # Unmarked (e.g. "IGST 18%"): a debited GST ledger is ITC, credited is output.
        direction = "inward" if want_debit else "outward"
    return direction, want_debit, 1


def _sum_voucher_side(voucher, want_debit: bool, sign: int) -> _GstTotals | None:
    """Total the taxable + GST on one side of a voucher, or None if that side
    has no line. The party ledger sits on the opposite side and is excluded."""
    totals = _GstTotals()
    touched = False
    for entry in voucher.entries:
        if entry.is_deemed_positive != want_debit:
            continue
        if _is_non_taxable_ledger(entry.ledger_name):
            continue  # TCS/TDS/round-off are not part of the taxable supply value
        component = _gst_component(entry.ledger_name)
        value = abs(entry.amount) * sign
        if component is None:
            totals.taxable += value
        else:
            setattr(totals, component, getattr(totals, component) + value)
        touched = True
    return totals if touched else None


def compute_tally_gst_detail(
    db: Session,
    company_id: uuid.UUID,
    tally_company_name: str,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
    service: TallyService | None = None,
    with_gstin: bool = False,
    with_reference: bool = False,
    with_hsn: bool = False,
) -> TallyGstDetailResult:
    """Read Tally vouchers and return outward/ITC totals plus per-voucher rows.

    Read-only: PAL never writes to Tally and stores nothing. Credit Notes (sales
    returns) and Debit Notes (purchase returns) are netted out of outward and ITC
    respectively. With ``with_gstin`` the party GSTIN is read from the ledger
    master and joined by party name. With ``with_reference`` the Supplier Invoice
    No. (voucher REFERENCE) is read from the Voucher Register and joined by GUID
    (the plain voucher collection does not expose it). Raises CompanyNotFoundError
    when company_id is not in PAL.
    """
    get_company(db, company_id)

    tally_company_name = tally_company_name.strip()
    owned_service = service is None
    svc: TallyService = service or get_tally_service()

    outward = _GstTotals()
    inward = _GstTotals()
    lines: list[TallyGstLine] = []
    fetched = used = 0
    error_message: str | None = None

    try:
        gstin_by_party: dict[str, str] = {}
        if with_gstin:
            # Party GSTIN lives on the ledger master, not the voucher.
            ledgers = svc.get_ledgers(tally_company_name)
            gstin_by_party = {
                led.name.casefold(): led.gstin
                for led in ledgers.ledgers
                if led.gstin
            }
        reference_by_guid: dict[str, str] = {}
        if with_reference:
            # Supplier Invoice No. (REFERENCE) only comes through the Voucher
            # Register, keyed by GUID.
            reference_by_guid = svc.get_voucher_references(
                tally_company_name,
                from_date=from_date.strftime("%Y%m%d") if from_date else None,
                to_date=to_date.strftime("%Y%m%d") if to_date else None,
            )
        stock_items = {}
        if with_hsn:
            # HSN + rate live on the stock item master; the voucher carries only
            # the item name, so join the two by name.
            stock_items = svc.get_stock_items(tally_company_name)
        #: HSN summary accumulator, keyed (hsn, rate, unit) -> taxable split by
        #: inter-state (IGST) vs intra-state (CGST+SGST), plus quantity.
        hsn_acc: dict[tuple[str, Decimal, str], dict[str, Decimal]] = {}
        result = svc.get_gst_vouchers(
            tally_company_name,
            from_date=from_date.strftime("%Y%m%d") if from_date else None,
            to_date=to_date.strftime("%Y%m%d") if to_date else None,
            with_inventory=with_hsn,
        )
        fetched = len(result.vouchers)
        for voucher in result.vouchers:
            if from_date and (voucher.date is None or voucher.date < from_date):
                continue
            if to_date and (voucher.date is None or voucher.date > to_date):
                continue
            classified = _classify_voucher_gst(voucher)
            if classified is None:
                continue
            direction, want_debit, sign = classified
            totals = _sum_voucher_side(voucher, want_debit, sign)
            if totals is None:
                continue
            bucket = outward if direction == "outward" else inward
            bucket.taxable += totals.taxable
            bucket.igst += totals.igst
            bucket.cgst += totals.cgst
            bucket.sgst += totals.sgst
            total_gst = totals.igst + totals.cgst + totals.sgst
            if with_hsn and direction == "outward":
                _accumulate_hsn(hsn_acc, voucher, stock_items, inter=bool(totals.igst), sign=sign)
            lines.append(
                TallyGstLine(
                    direction=direction,
                    date=voucher.date,
                    voucher_number=voucher.voucher_number,
                    voucher_type=voucher.voucher_type,
                    party_name=voucher.party_name,
                    gstin=gstin_by_party.get((voucher.party_name or "").casefold()),
                    reference=reference_by_guid.get((voucher.guid or "").strip()),
                    taxable=totals.taxable,
                    igst=totals.igst,
                    cgst=totals.cgst,
                    sgst=totals.sgst,
                    total_gst=total_gst,
                    invoice_value=totals.taxable + total_gst,
                )
            )
            used += 1
    except TallyError as exc:
        error_message = str(exc)
    finally:
        if owned_service:
            svc.close()

    return TallyGstDetailResult(
        success=error_message is None,
        tally_company_name=tally_company_name,
        from_date=from_date,
        to_date=to_date,
        outward=outward,
        inward_itc=inward,
        lines=lines,
        vouchers_fetched=fetched,
        vouchers_used=used,
        hsn_rows=_reconcile_hsn_to_outward(_build_hsn_rows(hsn_acc), outward)
        if with_hsn
        else [],
        error_message=error_message,
    )


def _accumulate_hsn(
    acc: dict[tuple[str, Decimal, str], dict[str, Decimal]],
    voucher,
    stock_items: dict,
    *,
    inter: bool,
    sign: int,
) -> None:
    """Add a voucher's inventory lines to the HSN summary accumulator.

    Each line's taxable value goes to the inter-state or intra-state bucket for
    its (HSN, rate, unit) group; ``sign`` is -1 for a sales return so it nets."""
    for inv in voucher.inventory:
        hsn = stock_items.get(inv.item.casefold()) or ""
        rate = inv.rate or Decimal(0)
        unit = inv.unit or ""
        # Tally already signs a credit-note's inventory negative; abs()*sign
        # matches _sum_voucher_side so a sales return nets out once, not twice.
        taxable = abs(inv.amount or Decimal(0)) * sign
        group = acc.setdefault(
            (hsn, rate, unit),
            {"inter": Decimal(0), "intra": Decimal(0), "qty": Decimal(0), "items": {}},
        )
        group["inter" if inter else "intra"] += taxable
        if inv.quantity is not None:
            group["qty"] += abs(inv.quantity) * sign
        group["items"][inv.item] = None  # dict preserves first-seen order, dedupes


def _build_hsn_rows(
    acc: dict[tuple[str, Decimal, str], dict[str, Decimal]]
) -> list[TallyHsnRow]:
    """Turn the HSN accumulator into rows, computing tax from the rate.

    IGST is charged on the inter-state taxable value; CGST and SGST each take
    half the rate on the intra-state value. Rows are ordered by HSN then rate.
    """
    cents = Decimal("0.01")
    rows: list[TallyHsnRow] = []
    for (hsn, rate, unit), g in sorted(acc.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        rows.append(
            TallyHsnRow(
                hsn=hsn,
                rate=rate,
                unit=unit,
                quantity=g["qty"],
                taxable=(g["inter"] + g["intra"]).quantize(cents, rounding=ROUND_HALF_UP),
                igst=(g["inter"] * rate / 100).quantize(cents, rounding=ROUND_HALF_UP),
                cgst=(g["intra"] * rate / 200).quantize(cents, rounding=ROUND_HALF_UP),
                sgst=(g["intra"] * rate / 200).quantize(cents, rounding=ROUND_HALF_UP),
                description=", ".join(g["items"]),
            )
        )
    return rows


def _reconcile_hsn_to_outward(
    rows: list[TallyHsnRow], outward: _GstTotals
) -> list[TallyHsnRow]:
    """Append one no-HSN row for the gap between the HSN summary and the outward
    totals, so the HSN sheet's taxable and tax totals equal the Sales sheet's.

    The HSN summary is built from stock-item inventory lines and its tax is
    re-derived from each item's rate. The Sales listing is built from the
    voucher ledger amounts. The two differ when an outward supply carries no
    stock item (a service), carries extra ledgers (freight, discount) that are
    not inventory, or when the per-rate rounding does not add back exactly. That
    difference is a real outward supply PAL cannot map to a stock-item HSN, so
    it is reported as a single reconciling row rather than silently dropped.
    """
    cents = Decimal("0.01")
    gap_taxable = outward.taxable - sum((r.taxable for r in rows), Decimal(0))
    gap_igst = outward.igst - sum((r.igst for r in rows), Decimal(0))
    gap_cgst = outward.cgst - sum((r.cgst for r in rows), Decimal(0))
    gap_sgst = outward.sgst - sum((r.sgst for r in rows), Decimal(0))
    gaps = [
        g.quantize(cents, rounding=ROUND_HALF_UP)
        for g in (gap_taxable, gap_igst, gap_cgst, gap_sgst)
    ]
    if all(g == 0 for g in gaps):
        return rows
    return rows + [
        TallyHsnRow(
            hsn="",
            rate=Decimal(0),
            unit="",
            quantity=Decimal(0),
            taxable=gaps[0],
            igst=gaps[1],
            cgst=gaps[2],
            sgst=gaps[3],
            description="Reconciliation to Sales total (no HSN in Tally)",
        )
    ]


def compute_tally_gst_working(
    db: Session,
    company_id: uuid.UUID,
    tally_company_name: str,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
    service: TallyService | None = None,
) -> TallyGstWorkingResult:
    """Outward output GST and inward ITC aggregated from Tally vouchers.

    Thin wrapper over :func:`compute_tally_gst_detail` that drops the per-row
    detail; the on-screen working uses only the totals.
    """
    detail = compute_tally_gst_detail(
        db,
        company_id,
        tally_company_name,
        from_date=from_date,
        to_date=to_date,
        service=service,
    )
    return TallyGstWorkingResult(
        success=detail.success,
        tally_company_name=detail.tally_company_name,
        from_date=detail.from_date,
        to_date=detail.to_date,
        outward=detail.outward,
        inward_itc=detail.inward_itc,
        vouchers_fetched=detail.vouchers_fetched,
        vouchers_used=detail.vouchers_used,
        error_message=detail.error_message,
    )


# --- TDS working from Tally vouchers ---------------------------------------

# Section codes PAL reports, and the keyword fallback when a TDS ledger name
# names no section explicitly. Mirrors the invoice TDS working.
_TDS_SECTION_CODES = ("194C", "194J", "194I", "194H", "194Q")
_TDS_SECTION_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("rent", "lease"), "194I"),
    (("profession", "consult", "technical", "audit", "legal", "fee"), "194J"),
    (("commission", "broker"), "194H"),
    (("contract", "transport", "advertis", "marketing", "labour"), "194C"),
]
_DEFAULT_TDS_SECTION = "194C"

# Standard section rates (%), used when computing TDS on an expense that has no
# booked TDS entry. Actual booked deductions use their own amount, not this.
_TDS_RATES: dict[str, Decimal] = {
    "194C": Decimal("2"),
    "194J": Decimal("10"),
    "194I": Decimal("10"),
    "194H": Decimal("5"),
    "194Q": Decimal("0.1"),
}


def _is_tds_ledger(name: str) -> bool:
    """True when a ledger name marks a TDS deduction ledger."""
    return "tds" in name.lower()


def _is_salary_tds(name: str) -> bool:
    """TDS on salary (s.192) — belongs on the Salary sheet, not Non Salary."""
    low = name.lower()
    return "tds" in low and "salary" in low


def _is_195_tds(name: str) -> bool:
    """TDS on foreign remittance (s.195) — belongs on the 195 sheet, not here."""
    low = name.lower()
    return "tds" in low and "195" in low


def _is_tax_ledger(name: str) -> bool:
    """A GST/tax ledger to exclude from the TDS base.

    ``_gst_component`` only catches igst/cgst/sgst by name; this also drops
    ledgers like "Ineligible GST Input" that carry GST but are named differently.
    """
    return _gst_component(name) is not None or "gst" in name.lower()


def _match_tds_section(text: str) -> str | None:
    """TDS section a ledger name maps to, or None when it attracts no TDS.

    Used to decide which *expense* ledgers to compute TDS on when Tally has no
    booked TDS entry. An explicit 194x code wins; else a keyword; else None.
    """
    upper = text.upper()
    for code in _TDS_SECTION_CODES:
        if code in upper:
            return code
    lower = text.lower()
    for keywords, section in _TDS_SECTION_KEYWORDS:
        if any(k in lower for k in keywords):
            return section
    return None


def _infer_tds_section(text: str) -> str:
    """Section for a known TDS ledger: a match, else the 194C default."""
    return _match_tds_section(text) or _DEFAULT_TDS_SECTION


@dataclass(frozen=True)
class TallyTdsLine:
    """One voucher's TDS deduction, for the Non Salary working."""

    voucher_date: date | None
    voucher_number: str | None
    party_name: str | None
    pan: str | None
    section: str
    base: Decimal
    tds: Decimal
    rate: Decimal  # fraction, tds / base
    status: str
    computed: bool = False  # True when TDS was computed, not read from a booked ledger
    voucher_type: str = ""
    supplier_invoice: str = ""  # voucher REFERENCE (supplier invoice no.)
    expense_ledger: str = ""
    tds_ledger: str = ""  # booked TDS ledger name, or a label derived from the section
    new_section: str = ""  # client's s.393 code for the section


# section -> (client "New Section" s.393 code, default TDS ledger label).
_SECTION_META: dict[str, tuple[str, str]] = {
    "194I": ("393(1)[Sl.6(iii).D(a)]", "TDS on Rent"),
    "194J": ("393(1)[Sl.6(iii).D(b)]", "TDS on Professional"),
    "194C": ("393(1)[Sl.6(iii).D(a)]", "TDS on Contract Section 393(1)"),
    "194H": ("393(1)[Sl.1(ii)]", "TDS on Commission"),
    "194Q": ("393(1)[Sl.6(i)]", "TDS on Purchase"),
}


def _section_meta(section: str) -> tuple[str, str]:
    return _SECTION_META.get(section, ("", ""))


@dataclass(frozen=True)
class TallyTdsWorkingResult:
    """TDS deductions read from Tally vouchers for a period (Non Salary sheet)."""

    success: bool
    tally_company_name: str
    from_date: date | None
    to_date: date | None
    lines: list[TallyTdsLine]
    total_tds: Decimal
    vouchers_fetched: int
    vouchers_used: int
    error_message: str | None = None


def compute_tally_tds_working(
    db: Session,
    company_id: uuid.UUID,
    tally_company_name: str,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
    service: TallyService | None = None,
    with_reference: bool = False,
    with_computed: bool = False,
) -> TallyTdsWorkingResult:
    """Read Tally vouchers and return the TDS deducted per voucher for a period.

    ``with_reference`` also reads the Supplier Invoice No. (voucher REFERENCE)
    from the Voucher Register — an extra, slow Tally call. The on-screen sync
    leaves it off; only the workbook export, which needs that column, turns it on.

    A voucher contributes when it has a TDS ledger entry (name containing "tds")
    with a non-zero amount and a non-zero expense base (the debit side, less the
    TDS and GST ledgers). The section is inferred from the TDS/expense ledger
    name, the rate is the actual tds / base, and the party PAN is read from the
    ledger master. Read-only; nothing is written to Tally or PAL. A Tally failure
    returns success=False so the UI can show it. Raises CompanyNotFoundError when
    the company is not in PAL.
    """
    get_company(db, company_id)

    tally_company_name = tally_company_name.strip()
    owned_service = service is None
    svc: TallyService = service or get_tally_service()

    lines: list[TallyTdsLine] = []
    total = ZERO
    fetched = used = 0
    error_message: str | None = None

    try:
        ledgers = svc.get_ledgers(tally_company_name)
        # PAN: the ledger's own PAN if set, else the 10 PAN characters embedded
        # in its GSTIN (positions 3-12), since Tally's ledger export carries the
        # GSTIN far more often than the PAN field.
        pan_by_party: dict[str, str] = {}
        for led in ledgers.ledgers:
            pan = led.pan
            if not pan and led.gstin and len(led.gstin) >= 12:
                pan = led.gstin[2:12]
            if pan:
                pan_by_party[led.name.casefold()] = pan
        # Supplier Invoice No. (voucher REFERENCE) only comes via the Voucher
        # Register, keyed by GUID. Skipped unless requested — it is a slow call.
        reference_by_guid: dict[str, str] = {}
        if with_reference:
            reference_by_guid = svc.get_voucher_references(
                tally_company_name,
                from_date=from_date.strftime("%Y%m%d") if from_date else None,
                to_date=to_date.strftime("%Y%m%d") if to_date else None,
            )
        result = svc.get_gst_vouchers(
            tally_company_name,
            from_date=from_date.strftime("%Y%m%d") if from_date else None,
            to_date=to_date.strftime("%Y%m%d") if to_date else None,
        )
        fetched = len(result.vouchers)
        for voucher in result.vouchers:
            if from_date and (voucher.date is None or voucher.date < from_date):
                continue
            if to_date and (voucher.date is None or voucher.date > to_date):
                continue
            pan = pan_by_party.get((voucher.party_name or "").casefold())
            status = "Exception" if not pan else "Pending"
            reference = reference_by_guid.get((voucher.guid or "").strip(), "")
            vtype = voucher.voucher_type or ""
            tds_entries = [e for e in voucher.entries if _is_tds_ledger(e.ledger_name)]
            # Salary TDS (s.192) and foreign-remittance TDS (s.195) are reported
            # on their own sheets, not on Non Salary.
            if any(_is_salary_tds(e.ledger_name) or _is_195_tds(e.ledger_name)
                   for e in tds_entries):
                continue
            # A Debit Note (purchase return) / Credit Note reverses a purchase:
            # the expense sits on the opposite side and the TDS is a reversal, so
            # it nets out instead of duplicating the original bill.
            is_return = (voucher.voucher_type or "").strip().lower() in (
                "debit note", "credit note")
            want_debit = not is_return
            sign = Decimal("-1") if is_return else Decimal("1")
            # Expense lines on the bill side (excluding TDS and every GST/tax
            # ledger, so the base is the taxable value, not the GST-inclusive total).
            expenses = [
                e for e in voucher.entries
                if e.is_deemed_positive == want_debit
                and not _is_tds_ledger(e.ledger_name)
                and not _is_tax_ledger(e.ledger_name)
            ]
            base = sign * sum((abs(e.amount) for e in expenses), ZERO)
            expense_ledger = expenses[0].ledger_name if expenses else ""

            if tds_entries:
                # TDS booked in Tally: use the actual deducted amount and rate.
                tds_amount = sign * sum((abs(e.amount) for e in tds_entries), ZERO)
                if tds_amount == ZERO or base == ZERO:
                    continue  # a TDS payment voucher, not a deduction — skip
                tds_ledger = tds_entries[0].ledger_name
                section = _infer_tds_section(tds_ledger + " " + expense_ledger)
                new_section, _ = _section_meta(section)
                lines.append(TallyTdsLine(
                    voucher_date=voucher.date,
                    voucher_number=voucher.voucher_number,
                    party_name=voucher.party_name,
                    pan=pan,
                    section=section,
                    base=base,
                    tds=tds_amount,
                    rate=round(tds_amount / base, 4),
                    status=status,
                    voucher_type=vtype,
                    supplier_invoice=reference,
                    expense_ledger=expense_ledger,
                    tds_ledger=tds_ledger,
                    new_section=new_section,
                ))
                total += tds_amount
                used += 1
                continue

            # No booked TDS. By default report only what Tally actually booked
            # (matches the client). ``with_computed`` optionally estimates TDS on
            # expense ledgers that attract it, at standard rates (used for months
            # where nothing is booked yet).
            if not with_computed:
                continue
            by_section: dict[str, Decimal] = {}
            ledger_of: dict[str, str] = {}
            for e in expenses:
                section = _match_tds_section(e.ledger_name)
                if section is None:
                    continue
                by_section[section] = by_section.get(section, ZERO) + abs(e.amount)
                ledger_of.setdefault(section, e.ledger_name)
            if not by_section:
                continue
            for section, sec_base in by_section.items():
                rate = _TDS_RATES[section]  # percent
                tds_amount = round(sec_base * rate / 100, 2)
                new_section, tds_label = _section_meta(section)
                lines.append(TallyTdsLine(
                    voucher_date=voucher.date,
                    voucher_number=voucher.voucher_number,
                    party_name=voucher.party_name,
                    pan=pan,
                    section=section,
                    base=sec_base,
                    tds=tds_amount,
                    rate=rate / 100,
                    status=status,
                    computed=True,
                    voucher_type=vtype,
                    supplier_invoice=reference,
                    expense_ledger=ledger_of.get(section, ""),
                    tds_ledger=tds_label,
                    new_section=new_section,
                ))
                total += tds_amount
            used += 1
    except TallyError as exc:
        error_message = str(exc)
    finally:
        if owned_service:
            svc.close()

    return TallyTdsWorkingResult(
        success=error_message is None,
        tally_company_name=tally_company_name,
        from_date=from_date,
        to_date=to_date,
        lines=lines,
        total_tds=total,
        vouchers_fetched=fetched,
        vouchers_used=used,
        error_message=error_message,
    )


def current_financial_year(today: date | None = None) -> tuple[date, date]:
    """Return the Indian financial year (1 April – 31 March) around ``today``."""
    today = today or date.today()
    start_year = today.year if today.month >= 4 else today.year - 1
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)


@dataclass(frozen=True)
class TallyBankDataSyncResult:
    """Outcome of one Tally bank-account + transaction sync run."""

    success: bool
    tally_company_name: str
    from_date: date
    to_date: date
    accounts_fetched: int
    accounts_created: int
    accounts_updated: int
    accounts_unchanged: int
    vouchers_fetched: int
    vouchers_matched: int
    statements_created: int
    transactions_created: int
    transactions_skipped: int
    error_message: str | None = None


def _counterparty_ledger(voucher, account_by_ledger: dict) -> str | None:
    """The non-bank ledger a Tally bank voucher posted against.

    A receipt/payment/contra voucher has the bank line plus one or more
    counterparty lines. The largest non-bank line is the head the money came
    from or went to — the historical ledger a future statement with the same
    narration should reuse. A split (several non-bank lines) just takes the
    biggest, which is enough signal for narration matching.
    """
    others = [
        entry
        for entry in voucher.entries
        if entry.ledger_name
        and account_by_ledger.get(entry.ledger_name.casefold()) is None
        and entry.amount != 0
    ]
    if not others:
        return None
    best = max(others, key=lambda entry: abs(entry.amount))
    return (best.ledger_name or "").strip() or None


def _running_balances(nets: list[Decimal], closing: Decimal) -> list[Decimal]:
    """Per-line balance from each line's net effect and the closing balance.

    The last line ends at ``closing``; each earlier line sits at the line after
    it minus that later line's net. The opening and closing totals are
    order-independent; only the intra-period middles depend on line order.
    """
    balances = [closing] * len(nets)
    running = closing
    for index in range(len(nets) - 1, -1, -1):
        balances[index] = running
        running = running - nets[index]
    return balances


def _entry_direction(entry, *, is_overdraft: bool) -> tuple[Decimal, Decimal]:
    """Map a Tally ledger line to PAL's (debit, credit) bank-statement amounts.

    In Tally a deemed-positive line is a debit of that ledger. For a normal
    bank account a debit increases the balance, which a bank statement calls a
    credit. An overdraft or credit-card ledger is the mirror image.
    """
    amount = abs(entry.amount)
    if entry.is_deemed_positive:
        return (ZERO, amount) if not is_overdraft else (amount, ZERO)
    return (amount, ZERO) if not is_overdraft else (ZERO, amount)


def sync_tally_bank_data(
    db: Session,
    company_id: uuid.UUID,
    tally_company_name: str,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
    service: TallyService | None = None,
) -> TallyBankDataSyncResult:
    """Import bank accounts and their bank-ledger vouchers from TallyPrime.

    The run is additive and idempotent: accounts are matched by ledger name,
    and each voucher line is stored with a stable ``external_id`` so a second
    sync of the same period creates nothing twice. PAL never writes to Tally.

    Raises CompanyNotFoundError when company_id is not in PAL.
    """
    get_company(db, company_id)

    tally_company_name = tally_company_name.strip()
    default_from, default_to = current_financial_year()
    period_from = from_date or default_from
    period_to = to_date or default_to
    if period_to < period_from:
        raise ValueError("The end date must not be before the start date.")

    owned_service = service is None
    svc: TallyService = service or get_tally_service()

    accounts_fetched = accounts_created = accounts_updated = accounts_unchanged = 0
    vouchers_fetched = vouchers_matched = 0
    statements_created = transactions_created = transactions_skipped = 0
    error_message: str | None = None

    try:
        ledger_result = svc.get_ledgers(tally_company_name)
        bank_ledgers = _bank_ledgers(ledger_result.ledgers)
        accounts_fetched = len(bank_ledgers)
        accounts_created, accounts_updated, accounts_unchanged = (
            _upsert_bank_accounts(db, company_id, bank_ledgers)
        )
        db.flush()

        # Ledger name → account, and ledger name → parent group, so direction
        # can be decided per account rather than guessed globally.
        accounts = list(
            db.scalars(
                select(BankAccount).where(BankAccount.company_id == company_id)
            ).all()
        )
        account_by_ledger = {
            (account.ledger_name or "").casefold(): account
            for account in accounts
            if (account.ledger_name or "").strip()
        }
        group_by_ledger = {
            (ledger.name or "").casefold(): (ledger.parent or "").strip().lower()
            for ledger in bank_ledgers
            if (ledger.name or "").strip()
        }
        # account id -> signed closing balance, so a running balance can be
        # filled for each line (Tally vouchers carry no running balance). Tally
        # reports the magnitude; a normal bank account holds a positive balance,
        # an overdraft a negative one.
        closing_by_account: dict[uuid.UUID, Decimal] = {}
        for ledger in bank_ledgers:
            account = account_by_ledger.get((ledger.name or "").casefold())
            if account is None or ledger.closing_balance is None:
                continue
            group = (ledger.parent or "").strip().lower()
            closing_by_account[account.id] = (
                ledger.closing_balance
                if group in _DEPOSIT_GROUPS
                else -ledger.closing_balance
            )

        voucher_result = svc.get_vouchers(tally_company_name)
        vouchers_fetched = len(voucher_result.vouchers)

        # account id → [(external_id, voucher, entry), ...]
        pending: dict[uuid.UUID, list[tuple[str, object, object]]] = {}
        for voucher in voucher_result.vouchers:
            if voucher.date is None or not (period_from <= voucher.date <= period_to):
                continue
            matched = False
            for index, entry in enumerate(voucher.entries):
                account = account_by_ledger.get(entry.ledger_name.casefold())
                if account is None or entry.amount == 0:
                    continue
                matched = True
                base = voucher.guid or voucher.voucher_number or "voucher"
                pending.setdefault(account.id, []).append(
                    (f"{base}:{index}", voucher, entry)
                )
            if matched:
                vouchers_matched += 1

        for account_id, rows in pending.items():
            statement = db.scalar(
                select(BankStatement).where(
                    BankStatement.company_id == company_id,
                    BankStatement.bank_account_id == account_id,
                    BankStatement.source == _TALLY_STATEMENT_SOURCE,
                    BankStatement.statement_start_date == period_from,
                    BankStatement.statement_end_date == period_to,
                )
            )
            if statement is None:
                statement = BankStatement(
                    company_id=company_id,
                    bank_account_id=account_id,
                    statement_start_date=period_from,
                    statement_end_date=period_to,
                    currency="INR",
                    source=_TALLY_STATEMENT_SOURCE,
                    status=BankStatementStatus.PROCESSED,
                )
                db.add(statement)
                db.flush()
                statements_created += 1

            existing = {
                row.external_id: row
                for row in db.scalars(
                    select(BankTransaction).where(
                        BankTransaction.bank_statement_id == statement.id,
                        BankTransaction.external_id.is_not(None),
                    )
                ).all()
            }

            for external_id, voucher, entry in rows:
                counterparty_ledger = _counterparty_ledger(voucher, account_by_ledger)
                prior = existing.get(external_id)
                if prior is not None:
                    # Backfill the historical ledger onto a row imported before
                    # this was captured, so narration matching can learn from it.
                    if counterparty_ledger and not prior.counterparty_ledger:
                        prior.counterparty_ledger = counterparty_ledger
                    transactions_skipped += 1
                    continue

                group = group_by_ledger.get(entry.ledger_name.casefold(), "")
                debit, credit = _entry_direction(
                    entry, is_overdraft=group not in _DEPOSIT_GROUPS
                )
                transaction = BankTransaction(
                    company_id=company_id,
                    bank_statement_id=statement.id,
                    transaction_date=voucher.date,
                    description=(
                        voucher.narration
                        or " ".join(
                            part
                            for part in (
                                voucher.voucher_type,
                                voucher.voucher_number,
                            )
                            if part
                        )
                        or None
                    ),
                    reference_number=voucher.voucher_number,
                    debit_amount=debit,
                    credit_amount=credit,
                    counterparty_ledger=counterparty_ledger,
                    is_inter_account_transfer=False,
                    external_id=external_id,
                    transaction_type=derive_transaction_type(debit, credit),
                )
                db.add(transaction)
                existing[external_id] = transaction
                transactions_created += 1

            # Fill a running balance for the whole statement (new + backfilled),
            # anchored on Tally's closing balance. Skipped when Tally gave none,
            # so a balance is never fabricated.
            closing = closing_by_account.get(account_id)
            if closing is not None:
                ordered = db.scalars(
                    select(BankTransaction)
                    .where(BankTransaction.bank_statement_id == statement.id)
                    .order_by(
                        BankTransaction.transaction_date, BankTransaction.created_at
                    )
                ).all()
                if ordered:
                    nets = [t.credit_amount - t.debit_amount for t in ordered]
                    for txn, balance in zip(ordered, _running_balances(nets, closing)):
                        txn.balance = balance
                    statement.opening_balance = closing - sum(nets, ZERO)
                    statement.closing_balance = closing

        db.commit()
    except TallyError as exc:
        db.rollback()
        error_message = str(exc)
    except Exception:
        db.rollback()
        raise
    finally:
        if owned_service:
            svc.close()

    return TallyBankDataSyncResult(
        success=error_message is None,
        tally_company_name=tally_company_name,
        from_date=period_from,
        to_date=period_to,
        accounts_fetched=accounts_fetched,
        accounts_created=accounts_created,
        accounts_updated=accounts_updated,
        accounts_unchanged=accounts_unchanged,
        vouchers_fetched=vouchers_fetched,
        vouchers_matched=vouchers_matched,
        statements_created=statements_created,
        transactions_created=transactions_created,
        transactions_skipped=transactions_skipped,
        error_message=error_message,
    )


__all__ = [
    "TallyBankDataSyncResult",
    "TallyBankSyncResult",
    "TallyCompanySyncResult",
    "TallySyncError",
    "current_financial_year",
    "get_latest_sync",
    "sync_tally_bank_accounts",
    "sync_tally_bank_data",
    "sync_tally_companies",
    "sync_tally_ledgers",
]
