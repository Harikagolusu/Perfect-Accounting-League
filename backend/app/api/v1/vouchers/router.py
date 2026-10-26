"""Draft voucher, review, approval, export and audit endpoints."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy.orm import Session

from app.api.v1.deps import company_or_404, get_actor
from app.core.database import get_db
from app.models.voucher import Voucher, VoucherSourceType, VoucherStatus, VoucherType
from app.schemas.voucher import (
    VoucherActionRequest,
    VoucherAuditEventResponse,
    VoucherCreate,
    VoucherExportRequest,
    VoucherFromBankRequest,
    VoucherFromInvoiceRequest,
    VoucherResponse,
    VoucherUpdate,
)
from app.services.accounting_audit import list_events
from app.services.bank import (
    BankAccountNotFoundError,
    BankStatementNotFoundError,
    BankTransactionNotFoundError,
)
from app.services.exports.common.workbook import content_disposition
from app.services.invoice import InvoiceNotFoundError
from app.services.ledger import LedgerNotFoundError
from app.services.voucher import (
    VoucherNotFoundError,
    approve_voucher,
    create_manual_voucher,
    delete_voucher,
    get_voucher,
    list_vouchers,
    reject_voucher,
    reopen_voucher,
    submit_voucher,
    update_voucher,
    verify_posting,
)
from app.services.voucher_export import voucher_export_file, voucher_export_preview
from app.services.voucher_factory import (
    create_voucher_from_bank_transaction,
    create_voucher_from_invoice,
)
from app.services.voucher_validation import (
    VoucherTransitionError,
    VoucherValidationError,
)

router = APIRouter()


def voucher_or_404(
    voucher_id: UUID = Path(description="Voucher id, scoped to this company."),
    company_id: UUID = Path(description="Owning company id."),
    db: Session = Depends(get_db),
) -> Voucher:
    try:
        return get_voucher(db, company_id, voucher_id)
    except VoucherNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Voucher not found"
        )


def _voucher_error(exc: Exception) -> None:
    if isinstance(exc, VoucherTransitionError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "INVALID_STATUS_TRANSITION",
                "message": str(exc),
                "issues": [],
            },
        ) from exc
    if isinstance(exc, VoucherValidationError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": exc.code,
                "message": str(exc),
                "issues": [issue.as_dict() for issue in exc.issues],
            },
        ) from exc
    raise exc


def _download_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": content_disposition(filename)},
    )


@router.get("/", response_model=list[VoucherResponse], summary="List vouchers")
def get_vouchers(
    company_id: UUID = Path(description="Owning company id."),
    status_filter: VoucherStatus | None = Query(default=None, alias="status"),
    voucher_type: VoucherType | None = Query(default=None),
    source_type: VoucherSourceType | None = Query(default=None),
    source_id: UUID | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: Session = Depends(get_db),
    _company=Depends(company_or_404),
) -> list[Voucher]:
    return list_vouchers(
        db,
        company_id,
        status=status_filter,
        voucher_type=voucher_type,
        source_type=source_type,
        source_id=source_id,
        date_from=date_from,
        date_to=date_to,
    )


@router.post(
    "/",
    response_model=VoucherResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a manual draft voucher",
)
def post_voucher(
    data: VoucherCreate,
    company_id: UUID = Path(description="Owning company id."),
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
    _company=Depends(company_or_404),
) -> Voucher:
    try:
        return create_manual_voucher(db, company_id, data, actor=actor)
    except LedgerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ledger not found"
        ) from exc


@router.post(
    "/from-invoice/{invoice_id}",
    response_model=VoucherResponse,
    summary="Create an idempotent draft voucher from an invoice",
)
def post_voucher_from_invoice(
    invoice_id: UUID = Path(description="Invoice id."),
    company_id: UUID = Path(description="Owning company id."),
    body: VoucherFromInvoiceRequest | None = None,
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
    _company=Depends(company_or_404),
) -> Voucher:
    try:
        return create_voucher_from_invoice(
            db,
            company_id,
            invoice_id,
            actor=(body.actor if body else actor),
        )
    except InvoiceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found"
        ) from exc
    except (VoucherValidationError, VoucherTransitionError) as exc:
        _voucher_error(exc)


@router.post(
    "/from-bank/{transaction_id}",
    response_model=VoucherResponse,
    summary="Create an idempotent draft voucher from a bank transaction",
)
def post_voucher_from_bank(
    transaction_id: UUID = Path(description="Bank transaction id."),
    company_id: UUID = Path(description="Owning company id."),
    body: VoucherFromBankRequest | None = None,
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
    _company=Depends(company_or_404),
) -> Voucher:
    try:
        return create_voucher_from_bank_transaction(
            db,
            company_id,
            transaction_id,
            actor=(body.actor if body else actor),
            split_lines=(body.split_lines if body else None),
        )
    except (
        BankTransactionNotFoundError,
        BankStatementNotFoundError,
        BankAccountNotFoundError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bank transaction not found"
        ) from exc
    except LedgerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ledger not found"
        ) from exc
    except (VoucherValidationError, VoucherTransitionError) as exc:
        _voucher_error(exc)


@router.get(
    "/export/preview",
    summary="Preview the approved-voucher import file",
)
def get_export_preview(
    company_id: UUID = Path(description="Owning company id."),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: Session = Depends(get_db),
    _company=Depends(company_or_404),
):
    return voucher_export_preview(
        db,
        company_id,
        date_from=date_from,
        date_to=date_to,
    )


@router.post("/export", summary="Generate the approved-voucher import file")
def post_export(
    body: VoucherExportRequest,
    company_id: UUID = Path(description="Owning company id."),
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
    _company=Depends(company_or_404),
) -> Response:
    result = voucher_export_file(
        db,
        company_id,
        voucher_ids=body.voucher_ids,
        date_from=body.date_from,
        date_to=body.date_to,
        actor=body.actor or actor,
    )
    return _download_response(result.content, result.filename)


@router.get(
    "/{voucher_id}",
    response_model=VoucherResponse,
    summary="Get a voucher",
)
def get_voucher_endpoint(voucher: Voucher = Depends(voucher_or_404)) -> Voucher:
    return voucher


@router.patch(
    "/{voucher_id}",
    response_model=VoucherResponse,
    summary="Edit a draft voucher",
)
def patch_voucher(
    data: VoucherUpdate,
    voucher: Voucher = Depends(voucher_or_404),
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
) -> Voucher:
    try:
        return update_voucher(db, voucher, data, actor=actor)
    except LedgerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ledger not found"
        ) from exc
    except (VoucherValidationError, VoucherTransitionError) as exc:
        _voucher_error(exc)


@router.delete(
    "/{voucher_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a draft voucher",
)
def delete_voucher_endpoint(
    voucher: Voucher = Depends(voucher_or_404),
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
) -> Response:
    try:
        delete_voucher(db, voucher, actor=actor)
    except (VoucherValidationError, VoucherTransitionError) as exc:
        _voucher_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{voucher_id}/submit",
    response_model=VoucherResponse,
    summary="Submit a draft for approval",
)
def post_submit(
    body: VoucherActionRequest | None = None,
    voucher: Voucher = Depends(voucher_or_404),
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
) -> Voucher:
    try:
        return submit_voucher(
            db, voucher, actor=(body.actor if body else actor)
        )
    except (VoucherValidationError, VoucherTransitionError) as exc:
        _voucher_error(exc)


@router.post(
    "/{voucher_id}/approve",
    response_model=VoucherResponse,
    summary="Approve a submitted voucher",
)
def post_approve(
    body: VoucherActionRequest | None = None,
    voucher: Voucher = Depends(voucher_or_404),
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
) -> Voucher:
    try:
        return approve_voucher(
            db,
            voucher,
            actor=(body.actor if body else actor),
            comment=(body.comment if body else None),
        )
    except (VoucherValidationError, VoucherTransitionError) as exc:
        _voucher_error(exc)


@router.post(
    "/{voucher_id}/reject",
    response_model=VoucherResponse,
    summary="Reject a voucher and return it for correction",
)
def post_reject(
    body: VoucherActionRequest | None = None,
    voucher: Voucher = Depends(voucher_or_404),
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
) -> Voucher:
    try:
        return reject_voucher(
            db,
            voucher,
            actor=(body.actor if body else actor),
            reason=(body.reason or body.comment) if body else None,
        )
    except (VoucherValidationError, VoucherTransitionError) as exc:
        _voucher_error(exc)


@router.post(
    "/{voucher_id}/reopen",
    response_model=VoucherResponse,
    summary="Reopen a rejected/failed voucher for correction",
)
def post_reopen(
    body: VoucherActionRequest | None = None,
    voucher: Voucher = Depends(voucher_or_404),
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
) -> Voucher:
    try:
        return reopen_voucher(db, voucher, actor=(body.actor if body else actor))
    except (VoucherValidationError, VoucherTransitionError) as exc:
        _voucher_error(exc)


@router.post(
    "/{voucher_id}/export",
    summary="Export one approved voucher as an import file",
)
def post_export_one(
    body: VoucherActionRequest | None = None,
    voucher: Voucher = Depends(voucher_or_404),
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
) -> Response:
    if voucher.status not in (
        VoucherStatus.APPROVED,
        VoucherStatus.AWAITING_IMPORT_CONFIRMATION,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "APPROVAL_REQUIRED",
                "message": (
                    f"Voucher {voucher.voucher_number} is in "
                    f"{voucher.status.value}; only approved vouchers can be exported."
                ),
                "issues": [],
            },
        )
    result = voucher_export_file(
        db,
        voucher.company_id,
        voucher_ids=[voucher.id],
        actor=(body.actor if body else actor),
    )
    return _download_response(result.content, result.filename)


@router.post(
    "/{voucher_id}/verify-posting",
    response_model=VoucherResponse,
    summary="Record verified posting or a posting failure",
)
def post_verify_posting(
    body: VoucherActionRequest,
    voucher: Voucher = Depends(voucher_or_404),
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
) -> Voucher:
    if body.verified is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "POSTING_VERIFICATION_REQUIRED",
                "message": "Set verified to true or false.",
                "issues": [],
            },
        )
    try:
        return verify_posting(
            db,
            voucher,
            actor=body.actor or actor,
            verified=body.verified,
            external_reference=body.external_reference,
            note=body.reason or body.comment,
        )
    except (VoucherValidationError, VoucherTransitionError) as exc:
        _voucher_error(exc)


@router.get(
    "/{voucher_id}/audit",
    response_model=list[VoucherAuditEventResponse],
    summary="List one voucher's append-only audit trail",
)
def get_voucher_audit(
    voucher: Voucher = Depends(voucher_or_404),
    db: Session = Depends(get_db),
) -> list:
    return list_events(db, voucher.company_id, voucher_id=voucher.id)


__all__ = ["router"]
