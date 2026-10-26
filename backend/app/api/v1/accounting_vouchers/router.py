"""Accounting-voucher output endpoints.

The workbook is generated from canonical purchase invoices and mapped bank
transactions. It never writes accounting rows back to the database; it is a
deterministic projection of what is already recorded.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.exports import ExportPreviewResponse
from app.services.company import CompanyNotFoundError
from app.services.exports.common.workbook import content_disposition
from app.services.exports.service import (
    accounting_voucher_export_file,
    accounting_voucher_export_preview,
)

router = APIRouter()


@router.get(
    "/export/preview",
    response_model=ExportPreviewResponse,
    summary="Preview the client accounting-voucher export",
)
def get_accounting_voucher_preview(
    company_id: UUID = Path(description="Owning company id."),
    date_from: date | None = Query(default=None, description="Earliest voucher date."),
    date_to: date | None = Query(default=None, description="Latest voucher date."),
    include_invoices: bool = Query(default=True),
    include_bank: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> ExportPreviewResponse:
    try:
        return accounting_voucher_export_preview(
            db,
            company_id,
            date_from=date_from,
            date_to=date_to,
            include_invoices=include_invoices,
            include_bank=include_bank,
        )
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        ) from None


@router.get(
    "/export.xlsx",
    summary="Download the client accounting-voucher workbook",
)
def get_accounting_voucher_workbook(
    company_id: UUID = Path(description="Owning company id."),
    date_from: date | None = Query(default=None, description="Earliest voucher date."),
    date_to: date | None = Query(default=None, description="Latest voucher date."),
    include_invoices: bool = Query(default=True),
    include_bank: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> Response:
    try:
        result = accounting_voucher_export_file(
            db,
            company_id,
            date_from=date_from,
            date_to=date_to,
            include_invoices=include_invoices,
            include_bank=include_bank,
        )
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        ) from None
    return Response(
        content=result.content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": content_disposition(result.filename)},
    )


__all__ = ["router"]
