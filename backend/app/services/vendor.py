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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.integrations.tally import TallyService
from app.models.vendor import Vendor, VendorStatus
from app.models.voucher import AccountingEntityType
from app.schemas.vendor import VendorCreate, VendorUpdate
from app.services.accounting_audit import record_event
from app.services.company import get_company
from app.services.ledger import sync_ledger_to_tally

#: A vendor is a party, so its ledger lives under Sundry Creditors in Tally.
_VENDOR_LEDGER_PARENT = "Sundry Creditors"


class VendorNotFoundError(Exception):
    """Raised when a vendor id does not resolve within the given company."""


class VendorConflictError(Exception):
    """Raised when a vendor with the same name already exists in the company."""


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


def _assert_unique_vendor_name(
    db: Session, company_id: uuid.UUID, name: str
) -> None:
    exists = db.scalar(
        select(Vendor.id)
        .where(
            Vendor.company_id == company_id,
            func.lower(Vendor.name) == name.strip().lower(),
        )
        .limit(1)
    )
    if exists is not None:
        raise VendorConflictError("A vendor with this name already exists.")


def create_vendor(
    db: Session,
    company_id: uuid.UUID,
    data: VendorCreate,
    *,
    service: TallyService | None = None,
) -> Vendor:
    """Create a vendor under `company_id` and its creditor ledger in Tally.

    Raises CompanyNotFoundError if the company does not exist (bad path id ->
    404), VendorConflictError on a duplicate name, and LedgerSyncError if Tally
    does not accept the creditor ledger. Block semantics: PAL persists only
    after Tally accepts the ledger, mirroring `create_ledger`.
    """
    company = get_company(db, company_id)
    _assert_unique_vendor_name(db, company_id, data.name)
    sync_ledger_to_tally(
        company,
        name=data.name,
        parent=_VENDOR_LEDGER_PARENT,
        gstin=data.gstin,
        service=service,
    )
    payload = data.model_dump()
    # creditor_ledger is left as given: approval learning / Tally ledger sync
    # link it, and pre-filling it would skip that review step.
    payload["parent_group"] = payload.get("parent_group") or _VENDOR_LEDGER_PARENT
    vendor = Vendor(company_id=company_id, **payload)
    db.add(vendor)
    record_event(
        db,
        company_id=company_id,
        entity_type=AccountingEntityType.VENDOR,
        entity_id=vendor.id,
        event_type="VENDOR_CREATED",
        new_value=vendor.name,
        reason="Vendor added to the Vendor Master.",
    )
    db.commit()
    db.refresh(vendor)
    return vendor


def update_vendor(db: Session, vendor: Vendor, data: VendorUpdate) -> Vendor:
    """Apply only the fields the client actually sent."""
    changed = [
        field
        for field, value in data.model_dump(exclude_unset=True).items()
        if getattr(vendor, field) != value
    ]
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(vendor, field, value)
    if changed:
        record_event(
            db,
            company_id=vendor.company_id,
            entity_type=AccountingEntityType.VENDOR,
            entity_id=vendor.id,
            event_type="VENDOR_UPDATED",
            new_value=vendor.name,
            reason="Vendor Master edited.",
            metadata={"fields": changed},
        )
    db.commit()
    db.refresh(vendor)
    return vendor


def deactivate_vendor(db: Session, vendor: Vendor) -> Vendor:
    """Soft delete: set status to INACTIVE, keeping the record for audit."""
    vendor.status = VendorStatus.INACTIVE
    record_event(
        db,
        company_id=vendor.company_id,
        entity_type=AccountingEntityType.VENDOR,
        entity_id=vendor.id,
        event_type="VENDOR_DEACTIVATED",
        new_status=VendorStatus.INACTIVE.value,
        new_value=vendor.name,
        reason="Vendor deactivated.",
    )
    db.commit()
    db.refresh(vendor)
    return vendor