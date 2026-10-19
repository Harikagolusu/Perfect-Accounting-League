"""Companies & Clients endpoints.

Thin HTTP layer: resolve the path resource, call `app.services.company`, return
the result. No queries live here.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.company import Company
from app.schemas.company import CompanyCreate, CompanyResponse, CompanyUpdate
from app.services.company import (
    CompanyNotFoundError,
    create_company,
    deactivate_company,
    get_company,
    list_companies,
    update_company,
)

router = APIRouter()


def company_or_404(
    company_id: UUID = Path(description="Company id."),
    db: Session = Depends(get_db),
) -> Company:
    """Resolve `{company_id}`, turning a missing record into a 404."""
    try:
        return get_company(db, company_id)
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        ) from None


@router.post(
    "",
    response_model=CompanyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a company",
)
def post_company(
    payload: CompanyCreate, db: Session = Depends(get_db)
) -> CompanyResponse:
    return create_company(db, payload)


@router.get("", response_model=list[CompanyResponse], summary="List companies")
def get_companies(
    include_inactive: bool = Query(
        default=False,
        description=(
            "Include INACTIVE (soft-deleted) companies. ACTIVE and ONBOARDING "
            "are both live states and are always returned."
        ),
    ),
    db: Session = Depends(get_db),
) -> list[CompanyResponse]:
    return list_companies(db, include_inactive=include_inactive)


@router.get("/{company_id}", response_model=CompanyResponse, summary="Get a company")
def get_company_by_id(company: Company = Depends(company_or_404)) -> CompanyResponse:
    return company


@router.patch(
    "/{company_id}", response_model=CompanyResponse, summary="Update a company"
)
def patch_company(
    payload: CompanyUpdate,
    company: Company = Depends(company_or_404),
    db: Session = Depends(get_db),
) -> CompanyResponse:
    return update_company(db, company, payload)


@router.delete(
    "/{company_id}",
    response_model=CompanyResponse,
    summary="Deactivate a company (soft delete)",
)
def delete_company(
    company: Company = Depends(company_or_404), db: Session = Depends(get_db)
) -> CompanyResponse:
    """Soft delete: sets status to INACTIVE so the record survives for audit."""
    return deactivate_company(db, company)