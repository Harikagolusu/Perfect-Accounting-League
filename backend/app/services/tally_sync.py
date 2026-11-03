"""Tally→PAL master data synchronisation service.

Fetches companies and ledgers from a live TallyPrime instance and upserts them
into PAL's ledger master for one company. Existing PAL accounting data (vouchers,
mappings, invoices) is never touched. MANUAL ledgers are never deleted.

Duplicate prevention: the existing unique constraint uq_ledgers_company_name on
(company_id, name) is the authoritative guard. The service does a case-insensitive
lookup before creating, so repeated syncs produce no duplicates.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

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


def _gst_component(ledger_name: str) -> str | None:
    """Return 'igst' | 'cgst' | 'sgst' for a GST tax ledger, else None.

    UTGST is reported alongside SGST. Cess and other heads are not GST
    components and return None (they fall into the taxable base — a reviewer
    corrects the rare voucher where that is wrong).
    """
    name = ledger_name.lower()
    if "igst" in name:
        return "igst"
    if "cgst" in name:
        return "cgst"
    if "sgst" in name or "utgst" in name:
        return "sgst"
    return None


def compute_tally_gst_working(
    db: Session,
    company_id: uuid.UUID,
    tally_company_name: str,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
    service: TallyService | None = None,
) -> TallyGstWorkingResult:
    """Read Tally vouchers and total output GST (sales) and input ITC (purchases).

    Read-only: PAL never writes to Tally and stores nothing. For a sales voucher
    the credited ledgers are output tax and taxable sales; for a purchase the
    debited ledgers are input tax and taxable purchases. The party ledger sits
    on the opposite side and is excluded. Other voucher types are ignored.

    Raises CompanyNotFoundError when company_id is not in PAL.
    """
    get_company(db, company_id)

    tally_company_name = tally_company_name.strip()
    owned_service = service is None
    svc: TallyService = service or get_tally_service()

    outward = _GstTotals()
    inward = _GstTotals()
    fetched = used = 0
    error_message: str | None = None

    try:
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
            # Direction from the GST tax ledgers, not the voucher-type name
            # (which is often a custom name like "Tax Invoice"). A credited GST
            # ledger is output tax (outward); a debited one is input tax (ITC).
            tax_credit = any(
                _gst_component(e.ledger_name) and not e.is_deemed_positive
                for e in voucher.entries
            )
            tax_debit = any(
                _gst_component(e.ledger_name) and e.is_deemed_positive
                for e in voucher.entries
            )
            if tax_credit and not tax_debit:
                bucket, want_debit = outward, False
            elif tax_debit and not tax_credit:
                bucket, want_debit = inward, True
            else:
                continue  # no GST, or both sides — left for a manual review
            touched = False
            for entry in voucher.entries:
                if entry.is_deemed_positive != want_debit:
                    continue
                component = _gst_component(entry.ledger_name)
                value = abs(entry.amount)
                if component is None:
                    bucket.taxable += value
                else:
                    setattr(bucket, component, getattr(bucket, component) + value)
                touched = True
            if touched:
                used += 1
    except TallyError as exc:
        error_message = str(exc)
    finally:
        if owned_service:
            svc.close()

    return TallyGstWorkingResult(
        success=error_message is None,
        tally_company_name=tally_company_name,
        from_date=from_date,
        to_date=to_date,
        outward=outward,
        inward_itc=inward,
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
