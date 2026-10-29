"""Bank Statements API module.

Thin HTTP layer: resolve path resources, call `app.services.bank`, and map
service errors onto safe status codes. Account numbers are masked by the
response schema, so no handler has to remember to hide them.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy.orm import Session

from app.api.v1.deps import get_actor
from app.core.database import get_db
from app.models.bank import (
    BankAccount,
    BankStatement,
    BankStatementStatus,
    BankTransaction,
)
from app.schemas.bank import (
    BankAccountCreate,
    BankAccountResponse,
    BankAccountUpdate,
    BankStatementCreate,
    BankStatementResponse,
    BankStatementUpdate,
    BankTransactionCreate,
    BankTransactionMappingUpdate,
    BankTransactionResponse,
    BankTransactionUpdate,
    ProcessStatementResponse,
)
from app.schemas.exports import ExportPreviewResponse
from app.schemas.voucher import LedgerSuggestionResponse
from app.services.bank import (
    BankAccountNotFoundError,
    BankStatementNotFoundError,
    BankTransactionNotFoundError,
    create_bank_account,
    create_bank_statement,
    create_transaction,
    delete_bank_account,
    delete_bank_statement,
    get_bank_account,
    get_bank_statement,
    get_transaction,
    list_bank_accounts,
    list_bank_statements,
    list_transactions,
    process_statement,
    statement_review_issues,
    update_bank_account,
    update_bank_statement,
    update_transaction,
    update_transaction_mapping,
)
from app.services.bank_validation import (
    BankTransitionError,
    BankValidationError,
)
from app.services.company import CompanyNotFoundError
from app.services.document import DocumentNotFoundError
from app.services.exports.common.workbook import content_disposition
from app.services.exports.service import bank_export_file, bank_export_preview
from app.services.ledger_suggestion import suggest_for_bank_transaction

router = APIRouter()


def _account_response(account: BankAccount) -> BankAccountResponse:
    """Build the response, replacing the account number with a masked form."""
    from app.schemas.bank import mask_account_number

    return BankAccountResponse(
        id=account.id,
        company_id=account.company_id,
        bank_name=account.bank_name,
        account_name=account.account_name,
        masked_account_number=mask_account_number(account.account_number) or "",
        ledger_name=account.ledger_name,
        currency=account.currency,
        is_active=account.is_active,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )


# --- Bank accounts ---------------------------------------------------------

def _require_account(db: Session, company_id: UUID, account_id: UUID) -> BankAccount:
    try:
        return get_bank_account(db, company_id, account_id)
    except BankAccountNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bank account not found"
        ) from None



@router.post(
    "/bank-accounts",
    response_model=BankAccountResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a bank account",
)
def post_bank_account(
    payload: BankAccountCreate,
    company_id: UUID = Path(description="Owning company id."),
    db: Session = Depends(get_db),
) -> BankAccountResponse:
    try:
        return _account_response(create_bank_account(db, company_id, payload))
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        ) from None


@router.get(
    "/bank-accounts", response_model=list[BankAccountResponse], summary="List bank accounts"
)
def get_bank_accounts(
    company_id: UUID = Path(description="Company to scope the listing to."),
    db: Session = Depends(get_db),
) -> list[BankAccountResponse]:
    return [_account_response(a) for a in list_bank_accounts(db, company_id)]


@router.get(
    "/bank-accounts/{account_id}",
    response_model=BankAccountResponse,
    summary="Get a bank account",
)
def get_bank_account_by_id(
    account_id: UUID = Path(description="Bank account id, scoped to this company."),
    company_id: UUID = Path(description="Owning company id."),
    db: Session = Depends(get_db),
) -> BankAccountResponse:
    return _account_response(_require_account(db, company_id, account_id))




@router.patch(
    "/bank-accounts/{account_id}",
    response_model=BankAccountResponse,
    summary="Update a bank account",
)
def patch_bank_account(
    payload: BankAccountUpdate,
    account_id: UUID = Path(description="Bank account id, scoped to this company."),
    company_id: UUID = Path(description="Owning company id."),
    db: Session = Depends(get_db),
) -> BankAccountResponse:
    try:
        return _account_response(update_bank_account(db, _require_account(db, company_id, account_id), payload))
    except BankAccountNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bank account not found"
        ) from None


@router.delete(
    "/bank-accounts/{account_id}",
    response_model=BankAccountResponse,
    summary="Deactivate a bank account",
)
def remove_bank_account(
    account_id: UUID = Path(description="Bank account id, scoped to this company."),
    company_id: UUID = Path(description="Owning company id."),
    db: Session = Depends(get_db),
) -> BankAccountResponse:
    try:
        return _account_response(delete_bank_account(db, company_id, account_id))
    except BankAccountNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bank account not found"
        ) from None


# --- Bank statements -------------------------------------------------------


def _require_statement(db: Session, company_id: UUID, statement_id: UUID) -> BankStatement:
    try:
        return get_bank_statement(db, company_id, statement_id)
    except BankStatementNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bank statement not found"
        ) from None


@router.post(
    "/bank-statements",
    response_model=BankStatementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a bank statement",
)
def post_bank_statement(
    payload: BankStatementCreate,
    company_id: UUID = Path(description="Company the statement belongs to."),
    db: Session = Depends(get_db),
) -> BankStatementResponse:
    try:
        return create_bank_statement(db, company_id, payload)
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        ) from None
    except BankAccountNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bank account not found"
        ) from None
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        ) from None
    except BankValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.message
        ) from None


@router.get(
    "/bank-statements",
    response_model=list[BankStatementResponse],
    summary="List bank statements",
)
def get_bank_statements(
    company_id: UUID = Path(description="Company to scope the listing to."),
    bank_account_id: UUID | None = Query(default=None, description="Filter by account."),
    status_filter: BankStatementStatus | None = Query(
        default=None, alias="status", description="Filter by statement status."
    ),
    db: Session = Depends(get_db),
) -> list[BankStatementResponse]:
    return list_bank_statements(
        db, company_id, bank_account_id=bank_account_id, status=status_filter
    )


@router.get(
    "/bank-statements/{statement_id}",
    response_model=BankStatementResponse,
    summary="Get a bank statement",
)
def get_bank_statement_by_id(
    statement_id: UUID = Path(description="Statement id, scoped to this company."),
    company_id: UUID = Path(description="Owning company id."),
    db: Session = Depends(get_db),
) -> BankStatementResponse:
    return _require_statement(db, company_id, statement_id)


@router.patch(
    "/bank-statements/{statement_id}",
    response_model=BankStatementResponse,
    summary="Update a bank statement",
)
def patch_bank_statement(
    payload: BankStatementUpdate,
    statement_id: UUID = Path(description="Statement id, scoped to this company."),
    company_id: UUID = Path(description="Owning company id."),
    db: Session = Depends(get_db),
) -> BankStatementResponse:
    try:
        return update_bank_statement(
            db, _require_statement(db, company_id, statement_id), payload
        )
    except BankAccountNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bank account not found"
        ) from None
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        ) from None
    except BankTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except BankValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.message
        ) from None


@router.delete(
    "/bank-statements/{statement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a statement and its transactions",
)
def remove_bank_statement(
    statement_id: UUID = Path(description="Statement id, scoped to this company."),
    company_id: UUID = Path(description="Owning company id."),
    db: Session = Depends(get_db),
) -> Response:
    """Removes the statement and its transactions. The document and the bank
    account are left in place."""
    try:
        delete_bank_statement(db, company_id, statement_id)
    except BankStatementNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bank statement not found"
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/bank-statements/{statement_id}/process",
    response_model=ProcessStatementResponse,
    summary="Attempt automatic statement parsing",
)
def process_bank_statement(
    statement_id: UUID = Path(description="Statement id, scoped to this company."),
    company_id: UUID = Path(description="Owning company id."),
    db: Session = Depends(get_db),
) -> ProcessStatementResponse:
    """Run AI extraction on the statement document and persist the results.

    When no AI provider is configured, reports that plainly and changes nothing.
    On success, sets the statement to PROCESSED or NEEDS_REVIEW and returns
    the transaction count and any review issues.
    """
    try:
        statement, result = process_statement(db, company_id, statement_id)
    except BankStatementNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bank statement not found"
        ) from None
    issues = statement_review_issues(db, statement).as_list()
    return ProcessStatementResponse(
        statement_id=statement.id,
        configured=result.configured,
        reason=result.reason,
        message=result.message or "",
        issues=issues,
        transaction_count=result.transaction_count,
    )


# --- Bank transactions -----------------------------------------------------


@router.get(
    "/bank-statements/{statement_id}/transactions",
    response_model=list[BankTransactionResponse],
    summary="List a statement's transactions",
)
def get_transactions(
    statement_id: UUID = Path(description="Statement id, scoped to this company."),
    company_id: UUID = Path(description="Owning company id."),
    date_from: date | None = Query(default=None, description="Earliest transaction date."),
    date_to: date | None = Query(default=None, description="Latest transaction date."),
    db: Session = Depends(get_db),
) -> list[BankTransactionResponse]:
    _require_statement(db, company_id, statement_id)
    return list_transactions(
        db, company_id, statement_id, transaction_date_from=date_from, transaction_date_to=date_to
    )


@router.post(
    "/bank-statements/{statement_id}/transactions",
    response_model=BankTransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a transaction to a statement",
)
def post_transaction(
    payload: BankTransactionCreate,
    statement_id: UUID = Path(description="Statement id, scoped to this company."),
    company_id: UUID = Path(description="Owning company id."),
    db: Session = Depends(get_db),
) -> BankTransactionResponse:
    try:
        return create_transaction(db, company_id, statement_id, payload)
    except BankStatementNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bank statement not found"
        ) from None
    except BankValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.message
        ) from None


def _require_transaction(
    db: Session, company_id: UUID, statement_id: UUID, transaction_id: UUID
) -> BankTransaction:
    try:
        return get_transaction(db, company_id, statement_id, transaction_id)
    except BankTransactionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found"
        ) from None


@router.get(
    "/bank-statements/{statement_id}/transactions/{transaction_id}",
    response_model=BankTransactionResponse,
    summary="Get a transaction",
)
def get_transaction_by_id(
    statement_id: UUID = Path(description="Statement id, scoped to this company."),
    company_id: UUID = Path(description="Owning company id."),
    transaction_id: UUID = Path(description="Transaction id."),
    db: Session = Depends(get_db),
) -> BankTransactionResponse:
    _require_statement(db, company_id, statement_id)
    return _require_transaction(db, company_id, statement_id, transaction_id)


@router.get(
    "/bank-statements/{statement_id}/transactions/{transaction_id}/ledger-suggestion",
    response_model=LedgerSuggestionResponse,
    summary="Explainable ledger suggestions for a bank transaction",
)
def get_transaction_ledger_suggestion(
    statement_id: UUID = Path(description="Statement id, scoped to this company."),
    company_id: UUID = Path(description="Owning company id."),
    transaction_id: UUID = Path(description="Transaction id."),
    db: Session = Depends(get_db),
) -> LedgerSuggestionResponse:
    return suggest_for_bank_transaction(
        db, company_id, statement_id, transaction_id
    )


@router.patch(
    "/bank-statements/{statement_id}/transactions/{transaction_id}",
    response_model=BankTransactionResponse,
    summary="Correct bank-fact fields on a transaction",
)
def patch_transaction(
    payload: BankTransactionUpdate,
    statement_id: UUID = Path(description="Statement id, scoped to this company."),
    company_id: UUID = Path(description="Owning company id."),
    transaction_id: UUID = Path(description="Transaction id."),
    db: Session = Depends(get_db),
) -> BankTransactionResponse:
    try:
        return update_transaction(db, company_id, statement_id, transaction_id, payload)
    except BankTransactionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found"
        ) from None


@router.patch(
    "/bank-statements/{statement_id}/transactions/{transaction_id}/mapping",
    response_model=BankTransactionResponse,
    summary="Confirm a transaction's counterparty ledger mapping",
)
def patch_transaction_mapping(
    payload: BankTransactionMappingUpdate,
    statement_id: UUID = Path(description="Statement id, scoped to this company."),
    company_id: UUID = Path(description="Owning company id."),
    transaction_id: UUID = Path(description="Transaction id."),
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
) -> BankTransactionResponse:
    try:
        return update_transaction_mapping(
            db, company_id, statement_id, transaction_id, payload, actor=actor
        )
    except BankTransactionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found"
        ) from None


@router.get(
    "/bank-transactions/export/preview",
    response_model=ExportPreviewResponse,
    summary="Preview the client bank-voucher export",
)
def get_bank_export_preview(
    company_id: UUID = Path(description="Owning company id."),
    statement_id: UUID | None = Query(
        default=None, description="Export one statement; omit to export all."
    ),
    date_from: date | None = Query(default=None, description="Earliest date."),
    date_to: date | None = Query(default=None, description="Latest date."),
    db: Session = Depends(get_db),
) -> ExportPreviewResponse:
    try:
        return bank_export_preview(
            db,
            company_id,
            statement_id=statement_id,
            date_from=date_from,
            date_to=date_to,
        )
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        ) from None
    except BankStatementNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bank statement not found"
        ) from None


@router.get(
    "/bank-transactions/export.xlsx",
    summary="Download the client bank-voucher workbook",
)
def get_bank_export_workbook(
    company_id: UUID = Path(description="Owning company id."),
    statement_id: UUID | None = Query(
        default=None, description="Export one statement; omit to export all."
    ),
    date_from: date | None = Query(default=None, description="Earliest date."),
    date_to: date | None = Query(default=None, description="Latest date."),
    db: Session = Depends(get_db),
) -> Response:
    try:
        result = bank_export_file(
            db,
            company_id,
            statement_id=statement_id,
            date_from=date_from,
            date_to=date_to,
        )
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        ) from None
    except BankStatementNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bank statement not found"
        ) from None
    return Response(
        content=result.content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": content_disposition(result.filename)},
    )