"""Canonical bank transactions -> balanced Receipt/Payment vouchers.

The bank line and its counterparty are two ledger rows of one voucher. A bank
amount is never used to invent a ledger: the bank account's chart-of-accounts
name and the transaction's confirmed counterparty ledger are both required.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bank import (
    BankAccount,
    BankStatement,
    BankTransaction,
    TransactionType,
)
from app.services.company import get_company
from app.services.exports.common.models import (
    ExportIssue,
    Voucher,
    VoucherBuild,
    VoucherLine,
)
from app.services.exports.common.validation import (
    assign_voucher_numbers,
    error,
    voucher_balance_issues,
)


def _text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _load_context(
    db: Session,
    company_id: uuid.UUID,
    transactions: list[BankTransaction],
) -> tuple[dict[uuid.UUID, BankStatement], dict[uuid.UUID, BankAccount]]:
    statement_ids = {transaction.bank_statement_id for transaction in transactions}
    statements = (
        {
            statement.id: statement
            for statement in db.scalars(
                select(BankStatement).where(
                    BankStatement.company_id == company_id,
                    BankStatement.id.in_(statement_ids),
                )
            ).all()
        }
        if statement_ids
        else {}
    )
    account_ids = {statement.bank_account_id for statement in statements.values()}
    accounts = (
        {
            account.id: account
            for account in db.scalars(
                select(BankAccount).where(
                    BankAccount.company_id == company_id,
                    BankAccount.id.in_(account_ids),
                )
            ).all()
        }
        if account_ids
        else {}
    )
    return statements, accounts


def build_bank_vouchers(
    db: Session,
    company_id: uuid.UUID,
    *,
    statement_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> VoucherBuild:
    """Build one two-line voucher per bank transaction.

    `statement_id`, when supplied, is checked against the company first so a
    statement from another company cannot be used to widen the query.
    """
    get_company(db, company_id)

    if statement_id is not None:
        exists = db.scalar(
            select(BankStatement.id).where(
                BankStatement.id == statement_id,
                BankStatement.company_id == company_id,
            )
        )
        if exists is None:
            from app.services.bank import BankStatementNotFoundError

            raise BankStatementNotFoundError(f"{company_id}/{statement_id}")

    query = select(BankTransaction).where(BankTransaction.company_id == company_id)
    if statement_id is not None:
        query = query.where(BankTransaction.bank_statement_id == statement_id)
    if date_from is not None:
        query = query.where(BankTransaction.transaction_date >= date_from)
    if date_to is not None:
        query = query.where(BankTransaction.transaction_date <= date_to)
    transactions = list(
        db.scalars(
            query.order_by(
                BankTransaction.transaction_date,
                BankTransaction.created_at,
                BankTransaction.id,
            )
        ).all()
    )

    statements, accounts = _load_context(db, company_id, transactions)
    vouchers: list[Voucher] = []
    issues: list[ExportIssue] = []

    for transaction in transactions:
        record_id = str(transaction.id)
        record_type = "BANK_TRANSACTION"
        transaction_issues: list[ExportIssue] = []

        statement = statements.get(transaction.bank_statement_id)
        if statement is None:
            transaction_issues.append(
                error(
                    "BANK_STATEMENT_REQUIRED",
                    "The transaction has no bank statement and cannot be exported.",
                    record_type=record_type,
                    record_id=record_id,
                )
            )
            issues.extend(transaction_issues)
            continue

        account = accounts.get(statement.bank_account_id)
        if account is None:
            transaction_issues.append(
                error(
                    "BANK_ACCOUNT_REQUIRED",
                    "The transaction's bank account could not be resolved.",
                    record_type=record_type,
                    record_id=record_id,
                )
            )
            issues.extend(transaction_issues)
            continue

        bank_ledger = _text(account.ledger_name)
        if not bank_ledger:
            transaction_issues.append(
                error(
                    "LEDGER_MAPPING_REQUIRED",
                    "The bank account has no ledger name for voucher export.",
                    record_type=record_type,
                    record_id=record_id,
                    field="Ledger Name",
                )
            )

        counterparty_ledger = _text(transaction.counterparty_ledger)
        if not counterparty_ledger:
            transaction_issues.append(
                error(
                    "BANK_MAPPING_REQUIRED",
                    "The transaction has no confirmed counterparty ledger.",
                    record_type=record_type,
                    record_id=record_id,
                    field="Ledger Name",
                )
            )

        is_receipt = transaction.transaction_type is TransactionType.CREDIT
        amount: Decimal = (
            transaction.credit_amount if is_receipt else transaction.debit_amount
        )
        if amount is None or amount <= Decimal("0"):
            transaction_issues.append(
                error(
                    "BANK_TRANSACTION_AMOUNT_REQUIRED",
                    "The transaction has no positive amount on its bank side.",
                    record_type=record_type,
                    record_id=record_id,
                    field="Ledger Amount",
                )
            )

        if any(issue.severity == "ERROR" for issue in transaction_issues):
            issues.extend(transaction_issues)
            continue

        bank_line = VoucherLine(
            ledger_name=bank_ledger,
            amount=amount,
            side="Dr" if is_receipt else "Cr",
        )
        counterparty_line = VoucherLine(
            ledger_name=counterparty_ledger,
            amount=amount,
            side="Cr" if is_receipt else "Dr",
            party_address=_text(transaction.counterparty_address),
            party_pincode=_text(transaction.counterparty_pincode),
            party_gstin=_text(transaction.counterparty_gstin),
        )
        voucher = Voucher(
            voucher_date=transaction.transaction_date,
            voucher_type="Receipt" if is_receipt else "Payment",
            change_mode="Accounting",
            source_type="BANK_TRANSACTION",
            source_id=record_id,
            lines=(bank_line, counterparty_line),
            narration=_text(transaction.description),
        )

        balance_issues = voucher_balance_issues(voucher)
        if balance_issues:
            issues.extend(balance_issues)
            continue

        vouchers.append(voucher)

    return VoucherBuild(
        vouchers=assign_voucher_numbers(vouchers),
        issues=tuple(issues),
    )


__all__ = ["build_bank_vouchers"]
