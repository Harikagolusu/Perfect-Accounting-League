"""Vendor business logic — the Vendor Master.

All database access for the Vendor entity lives here; routers stay thin.

Company scoping
---------------
Every read is filtered by `company_id`. There is deliberately no way to fetch a
vendor by id alone: `get_vendor` requires both, so asking for Company A's
vendors can never return Company B's, and asking for a vendor that belongs to
another company raises `VendorNotFoundError` exactly as a missing one does.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.vendor import Vendor, VendorStatus
from app.schemas.vendor import VendorCreate, VendorUpdate
from app.services.company import get_company


class VendorNotFoundError(Exception):
    """Raised when a vendor id does not resolve within the given company."""


def list_vendors(
    db: Session, company_id: uuid.UUID, *, include_inactive: bool = False
) -> list[Vendor]:
    """Return a company's vendors, newest first.

    Excludes INACTIVE (soft-deleted) vendors unless `include_inactive` is set.
    """
    stmt = (
        select(Vendor)
        .where(Vendor.company_id == company_id)
        .order_by(Vendor.created_at.desc(), Vendor.name)
    )
    if not include_inactive:
        stmt = stmt.where(Vendor.status != VendorStatus.INACTIVE)
    return list(db.scalars(stmt).all())


def get_vendor(db: Session, company_id: uuid.UUID, vendor_id: uuid.UUID) -> Vendor:
    """Return a vendor only if it belongs to `company_id`."""
    vendor = db.scalar(
        select(Vendor).where(Vendor.id == vendor_id, Vendor.company_id == company_id)
    )
    if vendor is None:
        raise VendorNotFoundError(f"{company_id}/{vendor_id}")
    return vendor


def create_vendor(
    db: Session, company_id: uuid.UUID, data: VendorCreate
) -> Vendor:
    """Create a vendor under `company_id`.

    Raises CompanyNotFoundError if the company does not exist, so a bad path
    id returns 404 rather than a database integrity error.
    """
    get_company(db, company_id)
    vendor = Vendor(company_id=company_id, **data.model_dump())
    db.add(vendor)
    db.commit()
    db.refresh(vendor)
    return vendor


def update_vendor(db: Session, vendor: Vendor, data: VendorUpdate) -> Vendor:
    """Apply only the fields the client actually sent."""
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(vendor, field, value)
    db.commit()
    db.refresh(vendor)
    return vendor


def deactivate_vendor(db: Session, vendor: Vendor) -> Vendor:
    """Soft delete: set status to INACTIVE, keeping the record for audit."""
    vendor.status = VendorStatus.INACTIVE
    db.commit()
    db.refresh(vendor)
    return vendor