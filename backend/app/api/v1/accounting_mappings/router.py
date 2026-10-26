"""Company-scoped accounting mapping API."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from app.api.v1.deps import company_or_404, get_actor
from app.core.database import get_db
from app.models.accounting_mapping import (
    AccountingMapping,
    AccountingMappingSourceType,
)
from app.schemas.accounting_mapping import (
    AccountingMappingCreate,
    AccountingMappingResponse,
    AccountingMappingUpdate,
)
from app.services.accounting_mapping import (
    AccountingMappingConflictError,
    AccountingMappingNotFoundError,
    create_mapping,
    deactivate_mapping,
    get_mapping,
    list_mappings,
    update_mapping,
)

router = APIRouter()


def mapping_or_404(
    mapping_id: UUID = Path(description="Mapping id, scoped to this company."),
    company_id: UUID = Path(description="Owning company id."),
    db: Session = Depends(get_db),
) -> AccountingMapping:
    try:
        return get_mapping(db, company_id, mapping_id)
    except AccountingMappingNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Accounting mapping not found"
        )


@router.get(
    "/", response_model=list[AccountingMappingResponse], summary="List accounting mappings"
)
def get_mappings(
    company_id: UUID = Path(description="Owning company id."),
    source_type: AccountingMappingSourceType | None = Query(default=None),
    include_inactive: bool = Query(default=False),
    db: Session = Depends(get_db),
    _company=Depends(company_or_404),
) -> list[AccountingMapping]:
    return list_mappings(
        db,
        company_id,
        source_type=source_type,
        include_inactive=include_inactive,
    )


@router.post(
    "/",
    response_model=AccountingMappingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an accounting mapping",
)
def post_mapping(
    data: AccountingMappingCreate,
    company_id: UUID = Path(description="Owning company id."),
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
    _company=Depends(company_or_404),
) -> AccountingMapping:
    try:
        return create_mapping(db, company_id, data, actor=actor)
    except AccountingMappingConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.get(
    "/{mapping_id}",
    response_model=AccountingMappingResponse,
    summary="Get an accounting mapping",
)
def get_mapping_endpoint(
    mapping: AccountingMapping = Depends(mapping_or_404),
) -> AccountingMapping:
    return mapping


@router.patch(
    "/{mapping_id}",
    response_model=AccountingMappingResponse,
    summary="Update an accounting mapping",
)
def patch_mapping(
    data: AccountingMappingUpdate,
    mapping: AccountingMapping = Depends(mapping_or_404),
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
) -> AccountingMapping:
    try:
        return update_mapping(db, mapping, data, actor=actor)
    except AccountingMappingConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.delete(
    "/{mapping_id}",
    response_model=AccountingMappingResponse,
    summary="Deactivate an accounting mapping",
)
def delete_mapping(
    mapping: AccountingMapping = Depends(mapping_or_404),
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
) -> AccountingMapping:
    return deactivate_mapping(db, mapping, actor=actor)


__all__ = ["router"]
