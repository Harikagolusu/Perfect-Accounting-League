"""Company-scoped Ledger Master API."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from app.api.v1.deps import company_or_404, get_actor
from app.core.database import get_db
from app.models.ledger import Ledger
from app.schemas.ledger import LedgerCreate, LedgerResponse, LedgerUpdate
from app.services.ledger import (
    LedgerConflictError,
    LedgerNotFoundError,
    create_ledger,
    deactivate_ledger,
    get_ledger,
    list_ledgers,
    update_ledger,
)

router = APIRouter()


def ledger_or_404(
    ledger_id: UUID = Path(description="Ledger id, scoped to this company."),
    company_id: UUID = Path(description="Owning company id."),
    db: Session = Depends(get_db),
) -> Ledger:
    try:
        return get_ledger(db, company_id, ledger_id)
    except LedgerNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ledger not found"
        )


@router.get("/", response_model=list[LedgerResponse], summary="List ledgers")
def get_ledgers(
    company_id: UUID = Path(description="Owning company id."),
    include_inactive: bool = Query(default=False),
    search: str | None = Query(default=None, max_length=255),
    db: Session = Depends(get_db),
    _company=Depends(company_or_404),
) -> list[Ledger]:
    return list_ledgers(
        db,
        company_id,
        include_inactive=include_inactive,
        search=search,
    )


@router.post(
    "/",
    response_model=LedgerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a ledger",
)
def post_ledger(
    data: LedgerCreate,
    company_id: UUID = Path(description="Owning company id."),
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
    _company=Depends(company_or_404),
) -> Ledger:
    try:
        return create_ledger(db, company_id, data, actor=actor)
    except LedgerConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.get("/{ledger_id}", response_model=LedgerResponse, summary="Get a ledger")
def get_ledger_endpoint(ledger: Ledger = Depends(ledger_or_404)) -> Ledger:
    return ledger


@router.patch(
    "/{ledger_id}",
    response_model=LedgerResponse,
    summary="Update a ledger",
)
def patch_ledger(
    data: LedgerUpdate,
    ledger: Ledger = Depends(ledger_or_404),
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
) -> Ledger:
    try:
        return update_ledger(db, ledger, data, actor=actor)
    except LedgerConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.delete(
    "/{ledger_id}",
    response_model=LedgerResponse,
    summary="Deactivate a ledger",
)
def delete_ledger(
    ledger: Ledger = Depends(ledger_or_404),
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
) -> Ledger:
    return deactivate_ledger(db, ledger, actor=actor)


__all__ = ["router"]
