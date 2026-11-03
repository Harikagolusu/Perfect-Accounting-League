"""Explainable ledger suggestions for invoices and bank transactions.

The suggestion engine is deliberately conservative. It only returns a ledger
when there is traceable evidence:

* an approved company mapping;
* a previously approved voucher for the same vendor;
* a configured vendor/company ledger name; or
* the confirmed bank counterparty ledger.

When none exists it records `NO_HISTORICAL_MAPPING` and, for the main
expense/sales line only, asks the AI to pre-select one of the company's
existing ledgers (see `app.services.ledger_ai_suggestion`). That choice is
flagged `AI_SUGGESTED_LEDGER` and keeps the voucher in `NEEDS_REVIEW`, so a
reviewer still confirms it. It never invents a ledger, tax rate or amount:
tax, party and round-off ledgers remain configuration-only.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.accounting_mapping import (
    TALLY_HISTORY_CONTEXT,
    AccountingMapping,
    AccountingMappingMatchType,
    AccountingMappingSourceType,
)
from app.models.bank import BankAccount, BankStatement, BankTransaction
from app.models.company import Company
from app.models.invoice import Invoice, InvoiceDirection
from app.models.invoice_line_item import InvoiceLineItem
from app.models.ledger import Ledger
from app.models.vendor import Vendor
from app.models.voucher import (
    Voucher,
    VoucherSide,
    VoucherSourceType,
    VoucherStatus,
    VoucherType,
)
from app.schemas.voucher import (
    LedgerSuggestion,
    LedgerSuggestionResponse,
    VoucherIssueResponse,
)
from app.services.bank import (
    BankAccountNotFoundError,
    BankStatementNotFoundError,
    get_transaction,
    list_transactions,
)
from app.services.company import get_company
from app.services.invoice import InvoiceNotFoundError, get_invoice
from app.services.ledger import find_ledger_by_name, list_ledgers
from app.services.ledger_ai_suggestion import (
    suggest_bank_ledger_with_ai,
    suggest_ledger_with_ai,
)

ZERO = Decimal("0")

#: Historical states whose ledger choices are safe to reuse as evidence.
_HISTORICAL_STATUSES = (
    VoucherStatus.APPROVED,
    VoucherStatus.AWAITING_IMPORT_CONFIRMATION,
    VoucherStatus.POSTED,
)


def _issue(code: str, message: str, field: str | None = None) -> VoucherIssueResponse:
    return VoucherIssueResponse(code=code, message=message, field=field)


def _configured_suggestion(
    db: Session,
    company_id: uuid.UUID,
    *,
    purpose: str,
    name: str | None,
    source: str,
    reason: str,
    exceptions: list[VoucherIssueResponse],
    missing_code: str = "MISSING_LEDGER",
    field: str | None = None,
) -> LedgerSuggestion | None:
    """Resolve a configured ledger name without inventing a master record."""
    if not name or not name.strip():
        return None
    ledger = find_ledger_by_name(db, company_id, name)
    if ledger is None:
        exceptions.append(
            _issue(
                missing_code,
                (
                    f"Configured ledger '{name}' is not in this company's Ledger "
                    "Master. Select or create the ledger before approval."
                ),
                field or purpose,
            )
        )
        return LedgerSuggestion(
            purpose=purpose,
            ledger_id=None,
            ledger_name=name,
            source=source,
            reason=reason,
            code=missing_code,
        )
    return LedgerSuggestion(
        purpose=purpose,
        ledger_id=ledger.id,
        ledger_name=ledger.name,
        source=source,
        reason=reason,
    )


def _ledger_suggestion(
    db: Session,
    company_id: uuid.UUID,
    *,
    purpose: str,
    ledger_id: uuid.UUID,
    source: str,
    reason: str,
    exceptions: list[VoucherIssueResponse],
) -> LedgerSuggestion | None:
    ledger = db.scalar(
        select(Ledger).where(
            Ledger.id == ledger_id, Ledger.company_id == company_id
        )
    )
    if ledger is None or not ledger.is_active:
        exceptions.append(
            _issue(
                "MISSING_LEDGER",
                f"The suggested ledger for {purpose} is not an active company ledger.",
                purpose,
            )
        )
        return None
    return LedgerSuggestion(
        purpose=purpose,
        ledger_id=ledger.id,
        ledger_name=ledger.name,
        source=source,
        reason=reason,
    )


def _mapping_evidence(
    db: Session,
    company_id: uuid.UUID,
    *,
    source_type: AccountingMappingSourceType,
    voucher_type: VoucherType,
    vendor_id: uuid.UUID | None,
    counterparty_name: str | None,
    narration: str | None,
) -> tuple[list[AccountingMapping], bool]:
    """Return active mappings matching the context and whether there is ambiguity."""
    query = select(AccountingMapping).where(
        AccountingMapping.company_id == company_id,
        AccountingMapping.source_type == source_type,
        AccountingMapping.is_active.is_(True),
        or_(
            AccountingMapping.voucher_type.is_(None),
            AccountingMapping.voucher_type == voucher_type.value,
        ),
    )
    evidence = []
    if vendor_id is not None:
        evidence.append(
            (
                AccountingMappingMatchType.VENDOR,
                AccountingMapping.vendor_id == vendor_id,
            )
        )
    if counterparty_name:
        evidence.append(
            (
                AccountingMappingMatchType.COUNTERPARTY,
                AccountingMapping.counterparty_name.ilike(counterparty_name.strip()),
            )
        )
    if narration:
        evidence.append(
            (
                AccountingMappingMatchType.NARRATION,
                AccountingMapping.narration_pattern.ilike(f"%{narration.strip()}%"),
            )
        )
    if not evidence:
        return [], False

    # One query per evidence kind keeps the SQL readable and lets the caller
    # distinguish identity matches from narration rules.
    found: list[AccountingMapping] = []
    for _match_type, condition in evidence:
        rows = list(db.scalars(query.where(condition).limit(20)).all())
        found.extend(rows)
    unique = {mapping.id: mapping for mapping in found}
    mappings = list(unique.values())
    # A mapping a person set up outranks one learned from Tally history, so a
    # manual choice is never reported as ambiguous against the history it was
    # meant to override. Ambiguity is judged only inside the winning tier.
    manual = [
        mapping
        for mapping in mappings
        if mapping.transaction_context != TALLY_HISTORY_CONTEXT
    ]
    tier = manual or mappings
    distinct_ledgers = {mapping.ledger_id for mapping in tier}
    return tier, len(distinct_ledgers) > 1


def _historical_invoice_ledger(
    db: Session,
    company_id: uuid.UUID,
    *,
    invoice: Invoice,
    voucher_type: VoucherType,
) -> tuple[uuid.UUID | None, bool]:
    """Find the ledger used on the last approved voucher for this vendor."""
    if invoice.vendor_id is None:
        return None, False
    vouchers = list(
        db.scalars(
            select(Voucher)
            .join(Invoice, Invoice.id == Voucher.source_id)
            .where(
                Voucher.company_id == company_id,
                Voucher.source_type == VoucherSourceType.INVOICE,
                Voucher.voucher_type == voucher_type,
                Voucher.status.in_(_HISTORICAL_STATUSES),
                Invoice.company_id == company_id,
                Invoice.vendor_id == invoice.vendor_id,
            )
            .order_by(Voucher.created_at.desc())
            .limit(5)
        ).all()
    )
    ledger_ids: set[uuid.UUID] = set()
    for voucher in vouchers:
        for line in voucher.lines:
            if line.line_type == "TAX" or line.ledger_id is None:
                continue
            if voucher_type is VoucherType.PURCHASE and line.side is VoucherSide.DR:
                ledger_ids.add(line.ledger_id)
            elif voucher_type is VoucherType.SALES and line.side is VoucherSide.CR:
                ledger_ids.add(line.ledger_id)
    if len(ledger_ids) == 1:
        return next(iter(ledger_ids)), False
    return None, len(ledger_ids) > 1


def _main_invoice_suggestion(
    db: Session,
    company_id: uuid.UUID,
    *,
    invoice: Invoice,
    company: Company,
    vendor: Vendor | None,
    voucher_type: VoucherType,
    purpose: str,
    exceptions: list[VoucherIssueResponse],
    invoice_items: list[InvoiceLineItem],
) -> LedgerSuggestion | None:
    source_type = AccountingMappingSourceType.INVOICE
    mappings, ambiguous = _mapping_evidence(
        db,
        company_id,
        source_type=source_type,
        voucher_type=voucher_type,
        vendor_id=invoice.vendor_id,
        counterparty_name=invoice.vendor_name,
        narration=invoice.invoice_number,
    )
    if ambiguous:
        exceptions.append(
            _issue(
                "AMBIGUOUS_ACCOUNTING_MAPPING",
                "Approved mapping history contains more than one ledger for this invoice.",
                purpose,
            )
        )
    elif mappings:
        mapping = sorted(mappings, key=lambda item: item.approved_at or item.created_at)[-1]
        suggestion = _ledger_suggestion(
            db,
            company_id,
            purpose=purpose,
            ledger_id=mapping.ledger_id,
            source="APPROVED_MAPPING",
            reason=mapping.reason,
            exceptions=exceptions,
        )
        if suggestion is not None:
            return suggestion

    ledger_id, historical_ambiguous = _historical_invoice_ledger(
        db, company_id, invoice=invoice, voucher_type=voucher_type
    )
    if historical_ambiguous:
        exceptions.append(
            _issue(
                "AMBIGUOUS_ACCOUNTING_MAPPING",
                "Approved voucher history contains more than one ledger for this vendor.",
                purpose,
            )
        )
    elif ledger_id is not None:
        suggestion = _ledger_suggestion(
            db,
            company_id,
            purpose=purpose,
            ledger_id=ledger_id,
            source="HISTORICAL_VOUCHER",
            reason="Last approved voucher for this vendor used this ledger.",
            exceptions=exceptions,
        )
        if suggestion is not None:
            return suggestion

    if voucher_type is VoucherType.PURCHASE and vendor is not None:
        suggestion = _configured_suggestion(
            db,
            company_id,
            purpose=purpose,
            name=vendor.default_expense_ledger,
            source="CONFIGURED_VENDOR_LEDGER",
            reason="Vendor Master default expense ledger.",
            exceptions=exceptions,
            field="Vendor Default Expense Ledger",
        )
        if suggestion is not None:
            return suggestion
    elif voucher_type is VoucherType.SALES:
        suggestion = _configured_suggestion(
            db,
            company_id,
            purpose=purpose,
            name=company.sales_ledger,
            source="CONFIGURED_COMPANY_LEDGER",
            reason="Company sales ledger configuration.",
            exceptions=exceptions,
            field="Company Sales Ledger",
        )
        if suggestion is not None:
            return suggestion

    # Last resort: let the AI pre-select from the existing ledger master so the
    # reviewer starts from a candidate instead of an empty dropdown. The
    # WARNING keeps the voucher in NEEDS_REVIEW until a person confirms it.
    ai = suggest_ledger_with_ai(
        db,
        company_id,
        purpose=purpose,
        invoice=invoice,
        items=invoice_items,
        vendor_name=vendor.name if vendor is not None else invoice.vendor_name,
        ledgers=list_ledgers(db, company_id),
    )
    if ai is not None:
        exceptions.append(
            VoucherIssueResponse(
                code="AI_SUGGESTED_LEDGER",
                message=(
                    f"'{ai.ledger_name}' was selected by AI from the ledger "
                    "master, not from approved history. Confirm or change it "
                    "before approval."
                ),
                severity="WARNING",
                field=purpose,
            )
        )
        return ai

    # Only now, with no deterministic match and no AI pick, is the line
    # genuinely unmapped. Raising this before the AI step made it fire even when
    # a ledger was found.
    exceptions.append(
        _issue(
            "NO_HISTORICAL_MAPPING",
            (
                "No approved mapping, approved voucher history or configured "
                f"{purpose.lower()} ledger is available."
            ),
            purpose,
        )
    )
    return None


def suggest_for_invoice(
    db: Session, company_id: uuid.UUID, invoice_id: uuid.UUID
) -> LedgerSuggestionResponse:
    invoice = get_invoice(db, company_id, invoice_id)
    company = get_company(db, company_id)
    vendor = (
        db.scalar(
            select(Vendor).where(
                Vendor.id == invoice.vendor_id, Vendor.company_id == company_id
            )
        )
        if invoice.vendor_id is not None
        else None
    )
    voucher_type = (
        VoucherType.SALES
        if invoice.direction is InvoiceDirection.SALES
        else VoucherType.PURCHASE
    )
    main_purpose = "SALES" if voucher_type is VoucherType.SALES else "EXPENSE"
    suggestions: list[LedgerSuggestion] = []
    exceptions: list[VoucherIssueResponse] = []

    if vendor is None and not invoice.vendor_name:
        exceptions.append(
            _issue(
                "MISSING_COUNTERPARTY",
                "The invoice has no vendor/customer identity, so no party ledger can be suggested.",
                "Vendor",
            )
        )

    invoice_items = list(
        db.scalars(
            select(InvoiceLineItem)
            .where(
                InvoiceLineItem.company_id == company_id,
                InvoiceLineItem.invoice_id == invoice.id,
            )
            .order_by(InvoiceLineItem.line_number)
        ).all()
    )

    main = _main_invoice_suggestion(
        db,
        company_id,
        invoice=invoice,
        company=company,
        vendor=vendor,
        voucher_type=voucher_type,
        purpose=main_purpose,
        exceptions=exceptions,
        invoice_items=invoice_items,
    )
    if main is not None:
        suggestions.append(main)

    party_name = vendor.creditor_ledger if vendor is not None else None
    party = _configured_suggestion(
        db,
        company_id,
        purpose="PARTY",
        name=party_name,
        source="CONFIGURED_VENDOR_LEDGER",
        reason="Vendor Master party/creditor ledger.",
        exceptions=exceptions,
        field="Vendor Creditor Ledger",
    )
    if party is not None:
        suggestions.append(party)

    tax_specs = (
        ("CGST", invoice.cgst_amount, company.cgst_ledger),
        ("SGST", invoice.sgst_amount, company.sgst_ledger),
        ("IGST", invoice.igst_amount, company.igst_ledger),
        ("CESS", invoice.cess_amount, company.cess_ledger),
    )
    for tax_name, amount, ledger_name in tax_specs:
        if amount is None or amount == ZERO:
            continue
        if not ledger_name:
            exceptions.append(
                _issue(
                    "MISSING_TAX_DATA",
                    (
                        f"The invoice has {tax_name} but the company has no "
                        f"{tax_name} ledger configured."
                    ),
                    f"{tax_name} Ledger",
                )
            )
            continue
        suggestion = _configured_suggestion(
            db,
            company_id,
            purpose=tax_name,
            name=ledger_name,
            source="CONFIGURED_COMPANY_LEDGER",
            reason=f"Company {tax_name} ledger configuration.",
            exceptions=exceptions,
            missing_code="MISSING_TAX_DATA",
            field=f"{tax_name} Ledger",
        )
        if suggestion is not None:
            suggestions.append(suggestion)

    if invoice.round_off is not None and invoice.round_off != ZERO:
        suggestion = _configured_suggestion(
            db,
            company_id,
            purpose="ROUND_OFF",
            name=company.round_off_ledger,
            source="CONFIGURED_COMPANY_LEDGER",
            reason="Company round-off ledger configuration.",
            exceptions=exceptions,
            field="Round Off Ledger",
        )
        if suggestion is not None:
            suggestions.append(suggestion)

    return LedgerSuggestionResponse(suggestions=suggestions, exceptions=exceptions)


def _historical_bank_ledger(
    db: Session,
    company_id: uuid.UUID,
    *,
    transaction: BankTransaction,
    voucher_type: VoucherType,
) -> tuple[uuid.UUID | None, bool]:
    if not transaction.counterparty_name:
        return None, False
    vouchers = list(
        db.scalars(
            select(Voucher)
            .join(BankTransaction, BankTransaction.id == Voucher.source_id)
            .where(
                Voucher.company_id == company_id,
                Voucher.source_type == VoucherSourceType.BANK_TRANSACTION,
                Voucher.voucher_type == voucher_type,
                Voucher.status.in_(_HISTORICAL_STATUSES),
                BankTransaction.company_id == company_id,
                BankTransaction.counterparty_name.ilike(
                    transaction.counterparty_name.strip()
                ),
            )
            .order_by(Voucher.created_at.desc())
            .limit(5)
        ).all()
    )
    ledger_ids: set[uuid.UUID] = set()
    for voucher in vouchers:
        for line in voucher.lines:
            if line.ledger_id is None:
                continue
            # The counterparty is on the side opposite the bank line. A bank
            # receipt has a credit counterparty; a payment has a debit one.
            if voucher_type is VoucherType.RECEIPT and line.side is VoucherSide.CR:
                ledger_ids.add(line.ledger_id)
            elif voucher_type is VoucherType.PAYMENT and line.side is VoucherSide.DR:
                ledger_ids.add(line.ledger_id)
            elif voucher_type is VoucherType.CONTRA:
                ledger_ids.add(line.ledger_id)
    if len(ledger_ids) == 1:
        return next(iter(ledger_ids)), False
    return None, len(ledger_ids) > 1


#: Generic words that carry no party identity, dropped before matching a bank
#: narration to a historical ledger. Bank narrations are mostly these plus a
#: party name; the party is the only thing worth matching on.
_NARRATION_STOPWORDS = frozenset(
    {
        "neft", "imps", "rtgs", "upi", "chq", "cheque", "clg", "from", "for", "via",
        "ref", "txn", "trf", "transfer", "account", "bank", "receipt", "payment", "pay",
        "pvt", "private", "ltd", "limited", "llp", "company", "corp", "inc", "india",
        "general", "insurance", "services", "service", "solutions", "technologies",
        "technology", "enterprises", "intl", "international", "pos", "card", "online",
        "payout", "charges", "charge", "deposit", "the", "and", "nonref",
    }
)


def _identity_tokens(text: str | None) -> set[str]:
    """Distinctive lowercase word tokens — party identity, no generic noise."""
    return {
        word
        for word in re.findall(r"[a-z]+", (text or "").lower())
        if len(word) >= 3 and word not in _NARRATION_STOPWORDS
    }


def _similar_historical_bank_ledger(
    db: Session,
    company_id: uuid.UUID,
    *,
    transaction: BankTransaction,
    exclude_tokens: frozenset[str] = frozenset(),
) -> LedgerSuggestion | None:
    """Match the narration's party to a ledger used for that party before.

    Tally bank-voucher narrations are terse ("Receipt 104"), so whole-narration
    similarity is useless. The reliable signal is the counterparty itself: the
    distinctive words in the bank narration against the distinctive words in
    each ledger name this company has historically posted bank entries to. A
    ledger wins only when it shares the most such words *and* is a clear winner
    — a tie (e.g. two branch ledgers of the same insurer) is left for a human.
    """
    # Drop the account holder's own name: a narration that merely repeats it
    # (own transfers, the company as remitter) names no counterparty.
    narration_tokens = _identity_tokens(transaction.description) - exclude_tokens
    if not narration_tokens:
        return None
    rows = db.execute(
        select(BankTransaction.counterparty_ledger, func.count())
        .where(
            BankTransaction.company_id == company_id,
            BankTransaction.id != transaction.id,
            BankTransaction.counterparty_ledger.is_not(None),
        )
        .group_by(BankTransaction.counterparty_ledger)
    ).all()
    # Rank by shared party words, then by how often the company has used that
    # ledger. The frequency tiebreak handles one party split across branch
    # ledgers (e.g. an insurer with per-state ledgers): the narration can't name
    # the branch, so the most-used one is the sensible default for a reviewer to
    # confirm. Only a dead-even tie on both word overlap and usage is unresolved.
    scored = sorted(
        (
            (len(narration_tokens & _identity_tokens(name)), count, name)
            for name, count in rows
        ),
        key=lambda item: (item[0], item[1]),
        reverse=True,
    )
    scored = [item for item in scored if item[0] >= 1]
    if not scored:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0] and scored[1][1] == scored[0][1]:
        return None
    best_name = scored[0][2]
    ledger = find_ledger_by_name(db, company_id, best_name)
    if ledger is None or not ledger.is_active:
        return None
    return LedgerSuggestion(
        purpose="COUNTERPARTY",
        ledger_id=ledger.id,
        ledger_name=ledger.name,
        source="SIMILAR_HISTORICAL",
        reason=f"The narration matches past bank entries posted to '{ledger.name}'.",
    )


def _narration_historical_bank_ledger(
    db: Session,
    company_id: uuid.UUID,
    *,
    transaction: BankTransaction,
    exclude_tokens: frozenset[str] = frozenset(),
) -> LedgerSuggestion | None:
    """Match the raw narration to a past mapped bank entry's narration.

    Compares the distinctive words in this narration against those in every
    earlier bank transaction that already carries a counterparty ledger, and
    reuses the ledger of the best-matching narration. A ledger wins only when
    it shares the most words and is a clear winner; a dead-even tie is left for
    a human. This catches narration→ledger links a ledger-name match misses
    (e.g. "SWIGGY" → "Food Expenses").
    """
    narration_tokens = _identity_tokens(transaction.description) - exclude_tokens
    if not narration_tokens:
        return None
    rows = db.execute(
        select(BankTransaction.description, BankTransaction.counterparty_ledger)
        .where(
            BankTransaction.company_id == company_id,
            BankTransaction.id != transaction.id,
            BankTransaction.counterparty_ledger.is_not(None),
            BankTransaction.description.is_not(None),
        )
    ).all()
    # Best word overlap per ledger, and how many past entries reached it (the
    # frequency tiebreak, as in _similar_historical_bank_ledger).
    best: dict[str, tuple[int, int]] = {}
    for description, ledger_name in rows:
        overlap = len(narration_tokens & _identity_tokens(description))
        if overlap < 1:
            continue
        current = best.get(ledger_name)
        if current is None or overlap > current[0]:
            best[ledger_name] = (overlap, 1)
        elif overlap == current[0]:
            best[ledger_name] = (overlap, current[1] + 1)
    if not best:
        return None
    scored = sorted(
        ((overlap, count, name) for name, (overlap, count) in best.items()),
        key=lambda item: (item[0], item[1]),
        reverse=True,
    )
    if len(scored) > 1 and scored[1][0] == scored[0][0] and scored[1][1] == scored[0][1]:
        return None
    best_name = scored[0][2]
    ledger = find_ledger_by_name(db, company_id, best_name)
    if ledger is None or not ledger.is_active:
        return None
    return LedgerSuggestion(
        purpose="COUNTERPARTY",
        ledger_id=ledger.id,
        ledger_name=ledger.name,
        source="SIMILAR_HISTORICAL",
        reason=f"The narration matches past bank entries posted to '{ledger.name}'.",
    )


def suggest_for_bank_transaction(
    db: Session,
    company_id: uuid.UUID,
    statement_id: uuid.UUID,
    transaction_id: uuid.UUID,
    *,
    use_ai: bool = True,
) -> LedgerSuggestionResponse:
    transaction = get_transaction(db, company_id, statement_id, transaction_id)
    statement = db.scalar(
        select(BankStatement).where(
            BankStatement.id == statement_id,
            BankStatement.company_id == company_id,
        )
    )
    if statement is None:
        raise BankStatementNotFoundError(f"{company_id}/{statement_id}")
    account = db.scalar(
        select(BankAccount).where(
            BankAccount.id == statement.bank_account_id,
            BankAccount.company_id == company_id,
        )
    )
    if account is None:
        raise BankAccountNotFoundError(f"{company_id}/{statement.bank_account_id}")

    voucher_type = (
        VoucherType.CONTRA
        if transaction.is_inter_account_transfer
        else (
            VoucherType.RECEIPT
            if transaction.credit_amount > transaction.debit_amount
            else VoucherType.PAYMENT
        )
    )
    suggestions: list[LedgerSuggestion] = []
    exceptions: list[VoucherIssueResponse] = []

    bank = _configured_suggestion(
        db,
        company_id,
        purpose="BANK",
        name=account.ledger_name,
        source="CONFIGURED_BANK_ACCOUNT_LEDGER",
        reason="Bank account ledger configuration.",
        exceptions=exceptions,
        field="Bank Account Ledger",
    )
    if bank is not None:
        suggestions.append(bank)
    else:
        exceptions.append(
            _issue(
                "MISSING_LEDGER",
                "The bank account has no ledger name configured.",
                "Bank Account Ledger",
            )
        )

    counterparty = _configured_suggestion(
        db,
        company_id,
        purpose="COUNTERPARTY",
        name=transaction.counterparty_ledger,
        source="CONFIRMED_COUNTERPARTY_LEDGER",
        reason="Human-confirmed counterparty ledger on the bank transaction.",
        exceptions=exceptions,
        field="Counterparty Ledger",
    )
    if counterparty is None:
        source_type = AccountingMappingSourceType.BANK_TRANSACTION
        mappings, ambiguous = _mapping_evidence(
            db,
            company_id,
            source_type=source_type,
            voucher_type=voucher_type,
            vendor_id=None,
            counterparty_name=transaction.counterparty_name,
            narration=transaction.description,
        )
        if ambiguous:
            exceptions.append(
                _issue(
                    "AMBIGUOUS_ACCOUNTING_MAPPING",
                    "Approved mapping history contains more than one ledger for this counterparty.",
                    "COUNTERPARTY",
                )
            )
        elif mappings:
            mapping = sorted(
                mappings, key=lambda item: item.approved_at or item.created_at
            )[-1]
            counterparty = _ledger_suggestion(
                db,
                company_id,
                purpose="COUNTERPARTY",
                ledger_id=mapping.ledger_id,
                source="APPROVED_MAPPING",
                reason=mapping.reason,
                exceptions=exceptions,
            )
        else:
            ledger_id, historical_ambiguous = _historical_bank_ledger(
                db, company_id, transaction=transaction, voucher_type=voucher_type
            )
            if historical_ambiguous:
                exceptions.append(
                    _issue(
                        "AMBIGUOUS_ACCOUNTING_MAPPING",
                        "Approved voucher history contains more than one ledger for this counterparty.",
                        "COUNTERPARTY",
                    )
                )
            elif ledger_id is not None:
                counterparty = _ledger_suggestion(
                    db,
                    company_id,
                    purpose="COUNTERPARTY",
                    ledger_id=ledger_id,
                    source="HISTORICAL_VOUCHER",
                    reason="Last approved voucher for this counterparty used this ledger.",
                    exceptions=exceptions,
                )
        if counterparty is None:
            company = get_company(db, company_id)
            company_tokens = frozenset(
                _identity_tokens(f"{company.name} {company.legal_name or ''}")
            )
            # Raw narration against the narration of past mapped bank entries
            # (old data) is the strongest signal; fall back to matching the
            # narration's party words against the ledger names themselves.
            counterparty = _narration_historical_bank_ledger(
                db,
                company_id,
                transaction=transaction,
                exclude_tokens=company_tokens,
            ) or _similar_historical_bank_ledger(
                db,
                company_id,
                transaction=transaction,
                exclude_tokens=company_tokens,
            )

        if use_ai and counterparty is None:
            ai = suggest_bank_ledger_with_ai(
                db,
                company_id,
                transaction=transaction,
                ledgers=list_ledgers(db, company_id),
            )
            if ai is not None:
                exceptions.append(
                    VoucherIssueResponse(
                        code="AI_SUGGESTED_LEDGER",
                        message=(
                            f"'{ai.ledger_name}' was selected by AI from the ledger "
                            "master, not from approved history. Confirm or change it "
                            "before approval."
                        ),
                        severity="WARNING",
                        field="COUNTERPARTY",
                    )
                )
                counterparty = ai

        if counterparty is None:
            exceptions.append(
                _issue(
                    "NO_HISTORICAL_MAPPING",
                    (
                        "No confirmed counterparty ledger, approved mapping, "
                        "approved voucher history or similar past transaction is "
                        "available."
                    ),
                    "COUNTERPARTY",
                )
            )
            exceptions.append(
                _issue(
                    "MISSING_COUNTERPARTY",
                    "The bank transaction has no confirmed counterparty ledger.",
                    "Counterparty Ledger",
                )
            )

    if counterparty is not None:
        suggestions.append(counterparty)

    return LedgerSuggestionResponse(suggestions=suggestions, exceptions=exceptions)


def suggest_ledgers_for_statement(
    db: Session, company_id: uuid.UUID, statement_id: uuid.UUID
) -> dict[str, str | None]:
    """Deterministic counterparty ledger per transaction, for the table column.

    No AI: only a confirmed ledger, an approved mapping, approved voucher
    history, or a similar past transaction — cheap enough to run for a whole
    statement, and it reflects what Tally history + the narration already imply.
    """
    suggestions: dict[str, str | None] = {}
    for txn in list_transactions(db, company_id, statement_id):
        if txn.counterparty_ledger:
            suggestions[str(txn.id)] = txn.counterparty_ledger
            continue
        response = suggest_for_bank_transaction(
            db, company_id, statement_id, txn.id, use_ai=False
        )
        counterparty = next(
            (s for s in response.suggestions if s.purpose == "COUNTERPARTY"), None
        )
        suggestions[str(txn.id)] = counterparty.ledger_name if counterparty else None
    return suggestions


__all__ = [
    "suggest_for_bank_transaction",
    "suggest_for_invoice",
    "suggest_ledgers_for_statement",
]
