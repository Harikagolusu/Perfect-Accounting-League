"""Create draft vouchers from canonical invoices and bank transactions.

Extraction is not repeated here. The factory reads the records the existing
invoice/bank modules already hold, asks the explainable suggestion service for
ledgers, and builds a draft. Missing evidence becomes a machine-readable issue
and `NEEDS_REVIEW`; it is never replaced with a guessed ledger or amount.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bank import BankAccount, BankStatement, BankTransaction
from app.models.company import Company
from app.models.invoice import Invoice, InvoiceDirection
from app.models.invoice_line_item import InvoiceLineItem
from app.models.ledger import Ledger
from app.models.vendor import Vendor
from app.models.voucher import (
    AccountingEntityType,
    Voucher,
    VoucherLine,
    VoucherSide,
    VoucherSourceType,
    VoucherStatus,
    VoucherType,
)
from app.schemas.voucher import VoucherLineInput
from app.services.accounting_audit import record_event
from app.services.bank import (
    BankAccountNotFoundError,
    BankStatementNotFoundError,
    BankTransactionNotFoundError,
)
from app.services.company import get_company
from app.services.invoice import get_invoice
from app.services.ledger import find_ledger_by_name
from app.services.ledger_suggestion import (
    suggest_for_bank_transaction,
    suggest_for_invoice,
)
from app.services.voucher import (
    make_line_from_input,
    next_voucher_number,
    persist_validation,
)
from app.services.voucher_validation import (
    VoucherIssue,
    VoucherValidationError,
    issues_as_dicts,
)

ZERO = Decimal("0")


def _issues_from_response(response) -> list[VoucherIssue]:
    return [
        VoucherIssue(
            code=issue.code,
            message=issue.message,
            field=issue.field,
            severity=issue.severity,
        )
        for issue in response.exceptions
    ]


def _suggestion_map(response) -> dict:
    return {suggestion.purpose: suggestion for suggestion in response.suggestions}


def _line_from_suggestion(
    company_id: uuid.UUID,
    *,
    line_number: int,
    suggestion,
    side: VoucherSide,
    amount: Decimal,
    line_type: str = "LEDGER",
    narration: str | None = None,
    item: InvoiceLineItem | None = None,
) -> VoucherLine:
    ledger_id = suggestion.ledger_id if suggestion is not None else None
    ledger_name = (suggestion.ledger_name if suggestion is not None else "") or ""
    return VoucherLine(
        company_id=company_id,
        line_number=line_number,
        ledger_id=ledger_id,
        ledger_name=ledger_name,
        side=side,
        amount=amount,
        narration=narration,
        line_type=line_type,
        item_name=item.description if item is not None else None,
        hsn_sac=item.hsn_sac if item is not None else None,
        billed_quantity=item.quantity if item is not None else None,
        item_amount=item.taxable_amount if item is not None else None,
        tax_type=None,
    )


def _invoice_line_plan(
    invoice: Invoice, *, is_sales: bool, main_purpose: str
) -> list[tuple[str, VoucherSide, Decimal, str]]:
    """The ordered (purpose, side, amount, line_type) a voucher gets.

    Shared by the builder and by `refresh_invoice_voucher_ledgers`, so a repair
    maps an existing line back to the purpose that created it instead of
    re-guessing from the amount — CGST and SGST are routinely equal.
    Zero and missing amounts produce no line, exactly as the builder does.
    """
    tax_side = VoucherSide.CR if is_sales else VoucherSide.DR
    plan: list[tuple[str, VoucherSide, Decimal | None, str]] = []
    if is_sales:
        plan.append(("PARTY", VoucherSide.DR, invoice.total_amount, "LEDGER"))
        plan.append((main_purpose, VoucherSide.CR, invoice.taxable_amount, "LEDGER"))
    else:
        plan.append((main_purpose, VoucherSide.DR, invoice.taxable_amount, "LEDGER"))

    for purpose, amount in (
        ("CGST", invoice.cgst_amount),
        ("SGST", invoice.sgst_amount),
        ("IGST", invoice.igst_amount),
        ("CESS", invoice.cess_amount),
    ):
        plan.append((purpose, tax_side, amount, "TAX"))

    if invoice.round_off is not None and invoice.round_off != ZERO:
        if is_sales:
            side = VoucherSide.CR if invoice.round_off > ZERO else VoucherSide.DR
        else:
            side = VoucherSide.DR if invoice.round_off > ZERO else VoucherSide.CR
        plan.append(("ROUND_OFF", side, abs(invoice.round_off), "LEDGER"))

    if not is_sales:
        plan.append(("PARTY", VoucherSide.CR, invoice.total_amount, "LEDGER"))

    return [
        (purpose, side, amount, line_type)
        for purpose, side, amount, line_type in plan
        if amount is not None and amount != ZERO
    ]


def refresh_invoice_voucher_ledgers(
    db: Session, company_id: uuid.UUID, voucher: Voucher
) -> int:
    """Fill a draft's unresolved ledger lines from today's suggestions.

    A voucher built before its mapping existed keeps an empty ledger line
    forever, because creation is idempotent and returns the existing draft.
    This fills only lines with no ledger, so a reviewer's own choice is never
    replaced, and only while the voucher is still editable. Amounts and sides
    are untouched: no line is added, removed or rebalanced. Returns the number
    of lines filled.
    """
    if voucher.status not in (VoucherStatus.DRAFT, VoucherStatus.NEEDS_REVIEW):
        return 0
    if not any(line.ledger_id is None for line in voucher.lines):
        return 0

    invoice = db.get(Invoice, voucher.source_id)
    if invoice is None or invoice.company_id != company_id:
        return 0

    is_sales = invoice.direction is InvoiceDirection.SALES
    main_purpose = "SALES" if is_sales else "EXPENSE"
    plan = _invoice_line_plan(invoice, is_sales=is_sales, main_purpose=main_purpose)
    lines = sorted(voucher.lines, key=lambda line: line.line_number)
    # A hand-edited voucher no longer matches the plan; leave it alone.
    if len(plan) != len(lines):
        return 0

    response = suggest_for_invoice(db, company_id, invoice.id)
    suggestions = _suggestion_map(response)

    filled = 0
    for (purpose, side, amount, line_type), line in zip(plan, lines):
        if line.ledger_id is not None:
            continue
        if line.side is not side or line.amount != amount or line.line_type != line_type:
            return 0
        suggestion = suggestions.get(purpose)
        if suggestion is None or suggestion.ledger_id is None:
            continue
        line.ledger_id = suggestion.ledger_id
        line.ledger_name = suggestion.ledger_name or ""
        filled += 1

    if not filled:
        return 0

    db.flush()
    issues = persist_validation(db, voucher, extra=_issues_from_response(response))
    # Re-derived from the filled lines: a voucher whose only complaint was the
    # missing ledger returns to DRAFT, one that still fails stays in review.
    voucher.status = VoucherStatus.NEEDS_REVIEW if issues else VoucherStatus.DRAFT
    record_event(
        db,
        company_id=company_id,
        entity_type=AccountingEntityType.VOUCHER,
        entity_id=voucher.id,
        voucher_id=voucher.id,
        event_type="VOUCHER_LEDGERS_RESUGGESTED",
        actor="local-user",
        previous_status=VoucherStatus.NEEDS_REVIEW.value,
        new_status=voucher.status.value,
        metadata={
            "lines_filled": filled,
            "issues": issues_as_dicts(issues),
        },
    )
    db.commit()
    db.refresh(voucher)
    return filled


def _existing_source_voucher(
    db: Session,
    company_id: uuid.UUID,
    source_type: VoucherSourceType,
    source_id: uuid.UUID,
) -> Voucher | None:
    return db.scalar(
        select(Voucher).where(
            Voucher.company_id == company_id,
            Voucher.source_type == source_type,
            Voucher.source_id == source_id,
        )
    )


def _create_voucher_record(
    db: Session,
    company_id: uuid.UUID,
    *,
    voucher_date,
    voucher_type: VoucherType,
    narration: str | None,
    source_type: VoucherSourceType,
    source_id: uuid.UUID | None,
    source_document_id: uuid.UUID | None,
    lines: list[VoucherLine],
    issues: list[VoucherIssue],
    actor: str,
    event_type: str,
    event_metadata: dict | None = None,
) -> Voucher:
    voucher = Voucher(
        company_id=company_id,
        voucher_date=voucher_date,
        voucher_type=voucher_type,
        voucher_number=next_voucher_number(db, company_id, voucher_type),
        status=VoucherStatus.DRAFT,
        narration=narration,
        source_type=source_type,
        source_id=source_id,
        source_document_id=source_document_id,
        created_by=actor or "local-user",
    )
    voucher.lines = lines
    db.add(voucher)
    db.flush()
    all_issues = persist_validation(db, voucher, extra=issues)
    if all_issues:
        voucher.status = VoucherStatus.NEEDS_REVIEW
    record_event(
        db,
        company_id=company_id,
        entity_type=AccountingEntityType.VOUCHER,
        entity_id=voucher.id,
        voucher_id=voucher.id,
        event_type=event_type,
        actor=actor,
        previous_status=None,
        new_status=voucher.status.value,
        metadata={
            "source_type": source_type.value,
            "source_id": str(source_id) if source_id else None,
            "source_document_id": str(source_document_id)
            if source_document_id
            else None,
            "issues": issues_as_dicts(all_issues),
            **(event_metadata or {}),
        },
    )
    db.commit()
    db.refresh(voucher)
    return voucher


def create_voucher_from_invoice(
    db: Session,
    company_id: uuid.UUID,
    invoice_id: uuid.UUID,
    *,
    actor: str = "local-user",
) -> Voucher:
    invoice = get_invoice(db, company_id, invoice_id)
    existing = _existing_source_voucher(
        db, company_id, VoucherSourceType.INVOICE, invoice.id
    )
    if existing is not None:
        # Creation is idempotent, so this is also the moment to pick up a
        # mapping that was learned after the draft was first built.
        refresh_invoice_voucher_ledgers(db, company_id, existing)
        return existing
    if invoice.invoice_date is None:
        raise VoucherValidationError(
            "MISSING_VOUCHER_DATE",
            "The invoice has no invoice date; PAL will not invent a voucher date.",
        )

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
    response = suggest_for_invoice(db, company_id, invoice.id)
    suggestions = _suggestion_map(response)
    issues = _issues_from_response(response)

    voucher_type = (
        VoucherType.SALES
        if invoice.direction is InvoiceDirection.SALES
        else VoucherType.PURCHASE
    )
    is_sales = voucher_type is VoucherType.SALES
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
    # The accounting voucher aggregates the invoice's taxable value onto one
    # ledger line. Item fields are only copied when the invoice has exactly one
    # item, so a multi-item invoice never shows one item's HSN/quantity against
    # the whole aggregate amount.
    item_for_line = invoice_items[0] if len(invoice_items) == 1 else None

    lines: list[VoucherLine] = []
    line_number = 1

    def add(
        purpose: str,
        side: VoucherSide,
        amount: Decimal | None,
        *,
        line_type: str = "LEDGER",
        item: InvoiceLineItem | None = None,
    ) -> None:
        nonlocal line_number
        if amount is None or amount == ZERO:
            return
        lines.append(
            _line_from_suggestion(
                company_id,
                line_number=line_number,
                suggestion=suggestions.get(purpose),
                side=side,
                amount=amount,
                line_type=line_type,
                item=item,
            )
        )
        line_number += 1

    party_purpose = "PARTY"
    main_purpose = "SALES" if is_sales else "EXPENSE"

    if invoice.taxable_amount is None:
        issues.append(
            VoucherIssue(
                "MISSING_AMOUNT",
                "The invoice has no taxable amount; the voucher cannot be balanced.",
                "Taxable Amount",
            )
        )
    if invoice.total_amount is None:
        issues.append(
            VoucherIssue(
                "MISSING_TOTAL",
                "The invoice has no total amount; no party line can be created.",
                "Total Amount",
            )
        )

    for purpose, side, amount, line_type in _invoice_line_plan(
        invoice, is_sales=is_sales, main_purpose=main_purpose
    ):
        if purpose not in suggestions:
            if line_type == "TAX":
                issues.append(
                    VoucherIssue(
                        "MISSING_TAX_DATA",
                        f"The invoice has {purpose} but no {purpose} ledger is configured.",
                        f"{purpose} Ledger",
                    )
                )
            elif purpose == "ROUND_OFF":
                issues.append(
                    VoucherIssue(
                        "MISSING_LEDGER",
                        "The invoice has a round-off amount but no round-off ledger is configured.",
                        "Round Off Ledger",
                    )
                )
        add(
            purpose,
            side,
            amount,
            line_type=line_type,
            # Item fields belong to the taxable line only.
            item=item_for_line if purpose == main_purpose else None,
        )
    if not any(line for line in lines if line.line_type != "TAX" and line.side is VoucherSide.CR) and not is_sales:
        issues.append(
            VoucherIssue(
                "MISSING_COUNTERPARTY",
                "No creditor/party ledger could be suggested for this invoice.",
                "Vendor Creditor Ledger",
            )
        )

    narration_parts = [invoice.invoice_number, invoice.vendor_name]
    narration = " - ".join(part for part in narration_parts if part) or "Purchase invoice"
    return _create_voucher_record(
        db,
        company_id,
        voucher_date=invoice.invoice_date,
        voucher_type=voucher_type,
        narration=narration,
        source_type=VoucherSourceType.INVOICE,
        source_id=invoice.id,
        source_document_id=invoice.document_id,
        lines=lines,
        issues=issues,
        actor=actor,
        event_type="VOUCHER_CREATED_FROM_INVOICE",
        event_metadata={
            "invoice_number": invoice.invoice_number,
            "direction": invoice.direction.value,
            "vendor_id": str(invoice.vendor_id) if invoice.vendor_id else None,
            "source_values": {
                "invoice_date": (
                    invoice.invoice_date.isoformat() if invoice.invoice_date else None
                ),
                "invoice_number": invoice.invoice_number,
                "vendor_name": invoice.vendor_name,
                "vendor_gstin": invoice.vendor_gstin,
                "taxable_amount": (
                    str(invoice.taxable_amount)
                    if invoice.taxable_amount is not None
                    else None
                ),
                "cgst_amount": (
                    str(invoice.cgst_amount)
                    if invoice.cgst_amount is not None
                    else None
                ),
                "sgst_amount": (
                    str(invoice.sgst_amount)
                    if invoice.sgst_amount is not None
                    else None
                ),
                "igst_amount": (
                    str(invoice.igst_amount)
                    if invoice.igst_amount is not None
                    else None
                ),
                "cess_amount": (
                    str(invoice.cess_amount)
                    if invoice.cess_amount is not None
                    else None
                ),
                "round_off": (
                    str(invoice.round_off) if invoice.round_off is not None else None
                ),
                "total_amount": (
                    str(invoice.total_amount)
                    if invoice.total_amount is not None
                    else None
                ),
            },
        },
    )


def _load_bank_source(
    db: Session, company_id: uuid.UUID, transaction_id: uuid.UUID
) -> tuple[BankTransaction, BankStatement, BankAccount]:
    transaction = db.scalar(
        select(BankTransaction).where(
            BankTransaction.id == transaction_id,
            BankTransaction.company_id == company_id,
        )
    )
    if transaction is None:
        raise BankTransactionNotFoundError(f"{company_id}/{transaction_id}")
    statement = db.scalar(
        select(BankStatement).where(
            BankStatement.id == transaction.bank_statement_id,
            BankStatement.company_id == company_id,
        )
    )
    if statement is None:
        raise BankStatementNotFoundError(
            f"{company_id}/{transaction.bank_statement_id}"
        )
    account = db.scalar(
        select(BankAccount).where(
            BankAccount.id == statement.bank_account_id,
            BankAccount.company_id == company_id,
        )
    )
    if account is None:
        raise BankAccountNotFoundError(f"{company_id}/{statement.bank_account_id}")
    return transaction, statement, account


def create_voucher_from_bank_transaction(
    db: Session,
    company_id: uuid.UUID,
    transaction_id: uuid.UUID,
    *,
    actor: str = "local-user",
    split_lines: list[VoucherLineInput] | None = None,
) -> Voucher:
    transaction, statement, account = _load_bank_source(
        db, company_id, transaction_id
    )
    existing = _existing_source_voucher(
        db, company_id, VoucherSourceType.BANK_TRANSACTION, transaction.id
    )
    if existing is not None:
        return existing
    if transaction.transaction_date is None:
        raise VoucherValidationError(
            "MISSING_VOUCHER_DATE",
            "The bank transaction has no date; PAL will not invent a voucher date.",
        )

    response = suggest_for_bank_transaction(
        db, company_id, statement.id, transaction.id
    )
    suggestions = _suggestion_map(response)
    issues = _issues_from_response(response)

    is_credit = transaction.credit_amount > transaction.debit_amount
    amount = (
        transaction.credit_amount if is_credit else transaction.debit_amount
    )
    if amount is None or amount <= ZERO:
        issues.append(
            VoucherIssue(
                "MISSING_AMOUNT",
                "The bank transaction has no positive amount.",
                "Amount",
            )
        )
        amount = ZERO

    if transaction.is_inter_account_transfer:
        voucher_type = VoucherType.CONTRA
    elif is_credit:
        voucher_type = VoucherType.RECEIPT
    else:
        voucher_type = VoucherType.PAYMENT

    bank_side = VoucherSide.DR if is_credit else VoucherSide.CR
    counterparty_side = VoucherSide.CR if is_credit else VoucherSide.DR
    lines: list[VoucherLine] = [
        _line_from_suggestion(
            company_id,
            line_number=1,
            suggestion=suggestions.get("BANK"),
            side=bank_side,
            amount=amount,
            narration="Bank account",
        )
    ]

    if split_lines:
        for offset, line_input in enumerate(split_lines, start=2):
            lines.append(
                make_line_from_input(db, company_id, line_input, offset)
            )
    else:
        lines.append(
            _line_from_suggestion(
                company_id,
                line_number=2,
                suggestion=suggestions.get("COUNTERPARTY"),
                side=counterparty_side,
                amount=amount,
                narration=transaction.counterparty_name,
            )
        )

    narration = (
        transaction.description
        or transaction.reference_number
        or f"Bank {voucher_type.value.lower()}"
    )
    return _create_voucher_record(
        db,
        company_id,
        voucher_date=transaction.transaction_date,
        voucher_type=voucher_type,
        narration=narration,
        source_type=VoucherSourceType.BANK_TRANSACTION,
        source_id=transaction.id,
        source_document_id=statement.document_id,
        lines=lines,
        issues=issues,
        actor=actor,
        event_type="VOUCHER_CREATED_FROM_BANK_TRANSACTION",
        event_metadata={
            "bank_account_id": str(account.id),
            "transaction_reference": transaction.reference_number,
            "inter_account_transfer": transaction.is_inter_account_transfer,
            "split_line_count": len(split_lines or []),
            "source_values": {
                "transaction_date": (
                    transaction.transaction_date.isoformat()
                    if transaction.transaction_date
                    else None
                ),
                "description": transaction.description,
                "reference_number": transaction.reference_number,
                "debit_amount": (
                    str(transaction.debit_amount)
                    if transaction.debit_amount is not None
                    else None
                ),
                "credit_amount": (
                    str(transaction.credit_amount)
                    if transaction.credit_amount is not None
                    else None
                ),
                "balance": (
                    str(transaction.balance)
                    if transaction.balance is not None
                    else None
                ),
                "counterparty_name": transaction.counterparty_name,
                "counterparty_ledger": transaction.counterparty_ledger,
                "counterparty_gstin": transaction.counterparty_gstin,
                "is_inter_account_transfer": transaction.is_inter_account_transfer,
            },
        },
    )


__all__ = [
    "create_voucher_from_bank_transaction",
    "create_voucher_from_invoice",
    "refresh_invoice_voucher_ledgers",
]
