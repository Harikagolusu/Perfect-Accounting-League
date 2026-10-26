"""Company business logic.

All database access for the Company entity lives here; routers stay thin and
only translate HTTP into service calls.

Status semantics
----------------
`status` is the single source of truth for a company's lifecycle. Soft delete is
`status = INACTIVE` -- there is no separate `is_active` flag to keep in sync.
Only INACTIVE is filtered out of default listings; ONBOARDING is a live state
and is always included.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company import Company, CompanyStatus
from app.models.voucher import AccountingEntityType
from app.schemas.company import CompanyCreate, CompanyUpdate
from app.services.accounting_audit import record_event


class CompanyNotFoundError(Exception):
    """Raised when a company id does not resolve to a record."""


def list_companies(db: Session, *, include_inactive: bool = False) -> list[Company]:
    """Return companies, newest first.

    Excludes INACTIVE (soft-deleted) companies unless `include_inactive` is set.
    """
    stmt = select(Company).order_by(Company.created_at.desc(), Company.name)
    if not include_inactive:
        stmt = stmt.where(Company.status != CompanyStatus.INACTIVE)
    return list(db.scalars(stmt).all())


def get_company(db: Session, company_id: uuid.UUID) -> Company:
    """Return a single company or raise CompanyNotFoundError."""
    company = db.get(Company, company_id)
    if company is None:
        raise CompanyNotFoundError(str(company_id))
    return company


def create_company(db: Session, data: CompanyCreate) -> Company:
    """Create a company from validated input."""
    company = Company(**data.model_dump())
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


_LEDGER_DEFAULT_FIELDS = {
    "cgst_ledger",
    "sgst_ledger",
    "igst_ledger",
    "cess_ledger",
    "round_off_ledger",
    "sales_ledger",
}


def update_company(
    db: Session,
    company: Company,
    data: CompanyUpdate,
    *,
    actor: str = "local-user",
) -> Company:
    """Apply only the fields the client actually sent.

    Company ledger defaults affect future deterministic voucher generation, so
    each changed default is appended to the accounting audit trail.
    """
    for field, value in data.model_dump(exclude_unset=True).items():
        old = getattr(company, field)
        if old == value:
            continue
        setattr(company, field, value)
        if field in _LEDGER_DEFAULT_FIELDS:
            record_event(
                db,
                company_id=company.id,
                entity_type=AccountingEntityType.LEDGER,
                entity_id=company.id,
                event_type="COMPANY_LEDGER_DEFAULT_UPDATED",
                actor=actor,
                field=field,
                old_value=old,
                new_value=value,
                reason="Company ledger default changed.",
            )
    db.commit()
    db.refresh(company)
    return company


def deactivate_company(db: Session, company: Company) -> Company:
    """Soft delete: set status to INACTIVE, keeping the record for audit."""
    company.status = CompanyStatus.INACTIVE
    db.commit()
    db.refresh(company)
    return company