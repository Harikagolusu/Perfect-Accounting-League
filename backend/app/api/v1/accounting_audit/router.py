"""Company-scoped accounting audit trail endpoint."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.v1.deps import company_or_404
from app.core.database import get_db
from app.models.voucher import AccountingEntityType
from app.schemas.voucher import VoucherAuditEventResponse
from app.services.accounting_audit import list_events

router = APIRouter()


@router.get(
    "/",
    response_model=list[VoucherAuditEventResponse],
    summary="List accounting audit events",
)
def get_accounting_audit(
    company_id: UUID = Path(description="Owning company id."),
    voucher_id: UUID | None = Query(default=None),
    entity_type: AccountingEntityType | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
    _company=Depends(company_or_404),
) -> list:
    return list_events(
        db,
        company_id,
        voucher_id=voucher_id,
        entity_type=entity_type,
        limit=limit,
    )


__all__ = ["router"]
