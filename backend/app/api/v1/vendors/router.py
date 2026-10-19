"""Vendor Master API module.

Thin HTTP layer: resolve the path resources, call `app.services.vendor`,
return the result. No queries live here.

Every route is nested under a company, so the company scope is part of the
address rather than something a handler has to remember.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.vendor import Vendor
from app.schemas.vendor import VendorCreate, VendorResponse, VendorUpdate
from app.services.company import CompanyNotFoundError
from app.services.vendor import (
    VendorNotFoundError,
    create_vendor,
    deactivate_vendor,
    get_vendor,
    list_vendors,
    update_vendor,
)

router = APIRouter()


def vendor_or_404(
    company_id: UUID = Path(description="Owning company id."),
    vendor_id: UUID = Path(description="Vendor id, scoped to this company."),
    db: Session = Depends(get_db),
) -> Vendor:
    """Resolve the vendor within the company, or 404."""
    try:
        return get_vendor(db, company_id, vendor_id)
    except VendorNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Vendor not found"
        ) from None


@router.post(
    "",
    response_model=VendorResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a vendor",
)
def post_vendor(
    payload: VendorCreate,
    company_id: UUID = Path(description="Company the vendor belongs to."),
    db: Session = Depends(get_db),
) -> VendorResponse:
    try:
        return create_vendor(db, company_id, payload)
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        ) from None


@router.get("", response_model=list[VendorResponse], summary="List a company's vendors")
def get_vendors(
    company_id: UUID = Path(description="Company to scope the listing to."),
    include_inactive: bool = Query(
        default=False,
        description=(
            "Include INACTIVE (soft-deleted) vendors. ACTIVE and NEEDS_MAPPING "
            "are both live states and are always returned."
        ),
    ),
    db: Session = Depends(get_db),
) -> list[VendorResponse]:
    return list_vendors(db, company_id, include_inactive=include_inactive)


@router.get("/{vendor_id}", response_model=VendorResponse, summary="Get a vendor")
def get_vendor_by_id(vendor: Vendor = Depends(vendor_or_404)) -> VendorResponse:
    return vendor


@router.patch(
    "/{vendor_id}", response_model=VendorResponse, summary="Update a vendor"
)
def patch_vendor(
    payload: VendorUpdate,
    vendor: Vendor = Depends(vendor_or_404),
    db: Session = Depends(get_db),
) -> VendorResponse:
    return update_vendor(db, vendor, payload)


@router.delete(
    "/{vendor_id}",
    response_model=VendorResponse,
    summary="Deactivate a vendor (soft delete)",
)
def delete_vendor(
    vendor: Vendor = Depends(vendor_or_404), db: Session = Depends(get_db)
) -> VendorResponse:
    """Soft delete: sets status to INACTIVE so the record survives for audit."""
    return deactivate_vendor(db, vendor)