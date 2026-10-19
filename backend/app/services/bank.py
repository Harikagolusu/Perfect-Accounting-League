"""Bank business logic — accounts, statements and transactions.

All database access for the bank entities lives here; routers stay thin.

Company scoping
---------------
Every read filters on `company_id`, and there is no lookup by id alone. A
statement, transaction or account belonging to another company is reported as
not found rather than refused, so a caller cannot probe for its existence.

Cross-entity integrity
----------------------
A statement may only reference an account and a document of the same company,
and a transaction may only reference a statement of that company. Without
this, one company's statement could point at another's document and every
per-company report would be quietly wrong.

Deletion
--------
Deleting a statement removes its transactions, which have no meaning without
it. It never touches the source document or the bank account: both outlive any
single statement.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bank import (
    BankAccount,
    BankStatement,
    BankStatementStatus,
    BankTransaction,
    TransactionType,
)
from app.schemas.bank import (
    BankAccountCreate,
    BankAccountUpdate,
    BankStatementCreate,
    BankStatementUpdate,
    BankTransactionCreate,
)
from app.services.bank_extraction import (
    StatementExtractionResult,
    get_statement_extractor,
)
from app.services.bank_validation import (
    BankTransitionError,
    BankValidationError,
    BankValidationResult,
    assert_transition_allowed,
    derive_transaction_type,
    reconcile_balances,
    reject_if_fatal,
    validate_statement,
    validate_transaction,
)
from app.services.company import get_company
from app.services.document import get_document

logger = logging.getLogger(__name__)

ZERO = Decimal("0")


class BankAccountNotFoundError(Exception):
    """Raised when a bank account does not resolve within the company."""


class BankStatementNotFoundError(Exception):
    """Raised when a bank statement does not resolve within the company."""


class BankTransactionNotFoundError(Exception):
    """Raised when a transaction does not resolve within the statement."""


# --- Bank accounts ---------------------------------------------------------


def list_bank_accounts(db: Session, company_id: uuid.UUID) -> list[BankAccount]:
    """Return a company's accounts, oldest first so the list is stable."""
    return list(
        db.scalars(
            select(BankAccount)
            .where(BankAccount.company_id == company_id)
            .order_by(BankAccount.created_at, BankAccount.bank_name)
        ).all()
    )


def get_bank_account(
    db: Session, company_id: uuid.UUID, account_id: uuid.UUID
) -> BankAccount:
    """Return an account only if it belongs to `company_id`."""
    account = db.scalar(
        select(BankAccount).where(
            BankAccount.id == account_id, BankAccount.company_id == company_id
        )
    )
    if account is None:
        raise BankAccountNotFoundError(str(account_id))
    return account


def create_bank_account(
    db: Session, company_id: uuid.UUID, data: BankAccountCreate
) -> BankAccount:
    get_company(db, company_id)
    account = BankAccount(company_id=company_id, **data.model_dump())
    db.add(account)
    db.commit()
    db.refresh(account)
    # The account number is deliberately not logged.
    logger.info(
        "Bank account created: company=%s account=%s bank=%s",
        company_id,
        account.id,
        account.bank_name,
    )
    return account


def update_bank_account(
    db: Session, account: BankAccount, data: BankAccountUpdate
) -> BankAccount:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(account, field, value)
    db.commit()
    db.refresh(account)
    return account


def delete_bank_account(
    db: Session, company_id: uuid.UUID, account_id: uuid.UUID
) -> BankAccount:
    """Deactivate an account.

    Accounts are deactivated rather than removed: statements reference them and
    must keep pointing somewhere meaningful.
    """
    account = get_bank_account(db, company_id, account_id)
    account.is_active = False
    db.commit()
    db.refresh(account)
    logger.info(
        "Bank account deactivated: company=%s account=%s", company_id, account_id
    )
    return account


# --- Bank statements -------------------------------------------------------


def list_bank_statements(
    db: Session,
    company_id: uuid.UUID,
    *,
    bank_account_id: uuid.UUID | None = None,
    status: BankStatementStatus | None = None,
) -> list[BankStatement]:
    """Return a company's statements, newest first."""
    query = (
        select(BankStatement)
        .where(BankStatement.company_id == company_id)
        .order_by(BankStatement.created_at.desc())
    )
    if bank_account_id is not None:
        query = query.where(BankStatement.bank_account_id == bank_account_id)
    if status is not None:
        query = query.where(BankStatement.status == status)
    return list(db.scalars(query).all())


def get_bank_statement(
    db: Session, company_id: uuid.UUID, statement_id: uuid.UUID
) -> BankStatement:
    """Return a statement only if it belongs to `company_id`."""
    statement = db.scalar(
        select(BankStatement).where(
            BankStatement.id == statement_id, BankStatement.company_id == company_id
        )
    )
    if statement is None:
        raise BankStatementNotFoundError(f"{company_id}/{statement_id}")
    return statement


def create_bank_statement(
    db: Session, company_id: uuid.UUID, data: BankStatementCreate
) -> BankStatement:
    """Create a statement for one of this company's accounts.

    Starts with zero transactions: nothing has parsed the document yet, and
    inventing lines would be dishonest.
    """
    get_company(db, company_id)
    # Both references must belong to this company, not merely exist.
    get_bank_account(db, company_id, data.bank_account_id)
    if data.document_id is not None:
        get_document(db, company_id, data.document_id)

    # Validate before writing, so a rejected statement never reaches the table.
    reject_if_fatal(
        validate_statement(
            statement_start_date=data.statement_start_date,
            statement_end_date=data.statement_end_date,
            opening_balance=data.opening_balance,
            closing_balance=data.closing_balance,
        )
    )

    statement = BankStatement(
        company_id=company_id,
        status=BankStatementStatus.UPLOADED,
        **data.model_dump(),
    )
    db.add(statement)
    db.commit()
    db.refresh(statement)

    review = validate_statement(
        statement_start_date=statement.statement_start_date,
        statement_end_date=statement.statement_end_date,
        opening_balance=statement.opening_balance,
        closing_balance=statement.closing_balance,
    )
    if review.issues:
        logger.info(
            "Bank statement %s stored with review flag(s): %s",
            statement.id,
            "; ".join(review.issues),
        )
    return statement


def update_bank_statement(
    db: Session, statement: BankStatement, data: BankStatementUpdate
) -> BankStatement:
    """Apply only the fields the client actually sent.

    The result is re-validated as a whole row, because a patch can combine with
    an existing value to produce an invalid statement that neither value alone
    implied.
    """
    payload = data.model_dump(exclude_unset=True)

    if payload.get("bank_account_id") is not None:
        get_bank_account(db, statement.company_id, payload["bank_account_id"])
    if payload.get("document_id") is not None:
        get_document(db, statement.company_id, payload["document_id"])

    # A statement always belongs to an account; clearing it would leave a row
    # that nothing can own.
    if "bank_account_id" in payload and payload["bank_account_id"] is None:
        raise BankValidationError("A bank statement must keep its bank account.")

    target_status = payload.pop("status", None)
    if target_status is not None and target_status is not statement.status:
        assert_transition_allowed(statement.status.value, target_status.value)
        statement.status = target_status

    for field, value in payload.items():
        setattr(statement, field, value)

    try:
        reject_if_fatal(
            validate_statement(
                statement_start_date=statement.statement_start_date,
                statement_end_date=statement.statement_end_date,
                opening_balance=statement.opening_balance,
                closing_balance=statement.closing_balance,
            )
        )
    except BankValidationError:
        # Leave the stored row exactly as it was rather than half-applied.
        db.rollback()
        db.refresh(statement)
        raise

    db.commit()
    db.refresh(statement)
    return statement


def delete_bank_statement(
    db: Session, company_id: uuid.UUID, statement_id: uuid.UUID
) -> BankStatement:
    """Delete a statement and its transactions.

    The source document and the bank account are left alone: both outlive any
    single statement.
    """
    statement = get_bank_statement(db, company_id, statement_id)
    transactions = list(
        db.scalars(
            select(BankTransaction).where(
                BankTransaction.bank_statement_id == statement_id
            )
        ).all()
    )
    for transaction in transactions:
        db.delete(transaction)
    # Flush the children before the parent: the database would also cascade,
    # and this keeps the removal done explicitly here and in that order.
    db.flush()
    db.delete(statement)
    db.commit()
    logger.info(
        "Bank statement deleted: company=%s statement=%s transactions=%d "
        "(document and account retained)",
        company_id,
        statement_id,
        len(transactions),
    )
    return statement


# --- Bank transactions -----------------------------------------------------


def list_transactions(
    db: Session,
    company_id: uuid.UUID,
    statement_id: uuid.UUID,
    *,
    transaction_date_from: date | None = None,
    transaction_date_to: date | None = None,
) -> list[BankTransaction]:
    """Return a statement's transactions in date order."""
    query = select(BankTransaction).where(
        BankTransaction.bank_statement_id == statement_id,
        BankTransaction.company_id == company_id,
    )
    if transaction_date_from is not None:
        query = query.where(BankTransaction.transaction_date >= transaction_date_from)
    if transaction_date_to is not None:
        query = query.where(BankTransaction.transaction_date <= transaction_date_to)
    query = query.order_by(BankTransaction.transaction_date, BankTransaction.created_at)
    return list(db.scalars(query).all())


def get_transaction(
    db: Session,
    company_id: uuid.UUID,
    statement_id: uuid.UUID,
    transaction_id: uuid.UUID,
) -> BankTransaction:
    """Return a transaction only if it sits inside this company's statement."""
    transaction = db.scalar(
        select(BankTransaction).where(
            BankTransaction.id == transaction_id,
            BankTransaction.bank_statement_id == statement_id,
            BankTransaction.company_id == company_id,
        )
    )
    if transaction is None:
        raise BankTransactionNotFoundError(
            f"{company_id}/{statement_id}/{transaction_id}"
        )
    return transaction


def create_transaction(
    db: Session,
    company_id: uuid.UUID,
    statement_id: uuid.UUID,
    data: BankTransactionCreate,
) -> BankTransaction:
    """Add one transaction to a statement.

    The direction is derived from the amounts rather than accepted from the
    client, so the type column can never disagree with the money.
    """
    statement = get_bank_statement(db, company_id, statement_id)

    debit = data.debit_amount or ZERO
    credit = data.credit_amount or ZERO
    review = validate_transaction(
        transaction_date=data.transaction_date,
        value_date=data.value_date,
        debit_amount=debit,
        credit_amount=credit,
        balance=data.balance,
    )
    reject_if_fatal(review)

    transaction = BankTransaction(
        company_id=company_id,
        bank_statement_id=statement.id,
        transaction_date=data.transaction_date,
        value_date=data.value_date,
        description=data.description,
        reference_number=data.reference_number,
        debit_amount=debit,
        credit_amount=credit,
        balance=data.balance,
        transaction_type=derive_transaction_type(debit, credit),
    )
    db.add(transaction)
    db.commit()
    db.refresh(transaction)

    if review.issues:
        logger.info(
            "Bank transaction %s stored with review flag(s): %s",
            transaction.id,
            "; ".join(review.issues),
        )
    return transaction


def statement_review_issues(
    db: Session, statement: BankStatement
) -> BankValidationResult:
    """Reconcile a statement's balances against its transactions.

    Read-only: a mismatch is reported for a human, never corrected.
    """
    transactions = list_transactions(db, statement.company_id, statement.id)
    return reconcile_balances(
        opening_balance=statement.opening_balance,
        closing_balance=statement.closing_balance,
        debits=[t.debit_amount for t in transactions],
        credits=[t.credit_amount for t in transactions],
    )


def process_statement(
    db: Session, company_id: uuid.UUID, statement_id: uuid.UUID
) -> tuple[BankStatement, StatementExtractionResult]:
    """Attempt automatic parsing of a statement.

    No parser is configured yet, so this says so plainly and changes nothing:
    the statement keeps its status and no transaction is invented.
    """
    statement = get_bank_statement(db, company_id, statement_id)
    extractor = get_statement_extractor()

    if not extractor.is_configured():
        # No status change on purpose: nothing was parsed, so claiming
        # PROCESSING or FAILED would misreport what happened.
        logger.info(
            "Extraction not configured; statement %s left as %s",
            statement_id,
            statement.status.value,
        )
        return statement, extractor.extract(statement, b"")

    from app.core.storage import resolve_storage_path

    raw = b""
    if statement.document_id is not None:
        document = get_document(db, company_id, statement.document_id)
        raw = resolve_storage_path(document.file_path).read_bytes()
    result = extractor.extract(statement, raw)
    logger.info(
        "Extraction attempted for statement %s: configured=%s",
        statement_id,
        result.configured,
    )
    return statement, result


__all__ = [
    "BankAccountNotFoundError",
    "BankStatementNotFoundError",
    "BankTransitionError",
    "BankTransactionNotFoundError",
    "BankValidationError",
    "TransactionType",
    "create_bank_account",
    "create_bank_statement",
    "create_transaction",
    "delete_bank_account",
    "delete_bank_statement",
    "get_bank_account",
    "get_bank_statement",
    "get_transaction",
    "list_bank_accounts",
    "list_bank_statements",
    "list_transactions",
    "process_statement",
    "statement_review_issues",
    "update_bank_account",
    "update_bank_statement",
]