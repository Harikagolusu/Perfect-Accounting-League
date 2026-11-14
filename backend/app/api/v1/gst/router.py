"""GST working endpoints.

The working is a read-only projection of the company's processed invoices, so
it is recomputed on every request and the same period always returns the same
numbers. Nothing is written to the database.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.gst import GstWorkingResponse, GstWorkingRowResponse
from app.services.company import CompanyNotFoundError
from app.services.gst_working import compute_invoice_gst_working

router = APIRouter()


@router.get(
    "/working",
    response_model=GstWorkingResponse,
    summary="GSTR-1 and GSTR-3B workings computed from processed invoices",
)
def get_gst_working(
    company_id: UUID = Path(description="Owning company id."),
    date_from: date | None = Query(default=None, description="Earliest invoice date."),
    date_to: date | None = Query(default=None, description="Latest invoice date."),
    db: Session = Depends(get_db),
) -> GstWorkingResponse:
    if date_from and date_to and date_to < date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The end date must not be before the start date.",
        )
    try:
        result = compute_invoice_gst_working(
            db, company_id, from_date=date_from, to_date=date_to
        )
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        ) from None
    return GstWorkingResponse(
        company_id=result.company_id,
        from_date=result.from_date,
        to_date=result.to_date,
        invoices_used=result.invoices_used,
        gstr1=[GstWorkingRowResponse(category=r.category, **r.totals.__dict__) for r in result.gstr1],
        gstr3b=[GstWorkingRowResponse(category=r.category, **r.totals.__dict__) for r in result.gstr3b],
    )


__all__ = ["router"]
