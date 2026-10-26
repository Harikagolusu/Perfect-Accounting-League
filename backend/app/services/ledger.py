"""Ledger Master business logic.

Every read and write is company-scoped. A ledger id from another company is
indistinguishable from a missing one, exactly as in the Vendor/Document
services.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ledger import Ledger, LedgerSource
from app.models.voucher import AccountingEntityType
from app.schemas.ledger import LedgerCreate, LedgerUpdate
from app.services.accounting_audit import record_event
from app.services.company import get_company


class LedgerNotFoundError(Exception):
    """Raised when a ledger id does not resolve within the company."""


class LedgerConflictError(Exception):
    """Raised for a duplicate or otherwise conflicting ledger name/code."""


def list_ledgers(
    db: Session,
    company_id: uuid.UUID,
    *,
    include_inactive: bool = False,
    search: str | None = None,
) -> list[Ledger]:
    query = select(Ledger).where(Ledger.company_id == company_id)
    if not include_inactive:
        query = query.where(Ledger.is_active.is_(True))
    if search:
        term = f"%{search.strip()}%"
        query = query.where(
            Ledger.name.ilike(term) | Ledger.code.ilike(term)
        )
    query = query.order_by(Ledger.name, Ledger.created_at)
    return list(db.scalars(query).all())


def get_ledger(db: Session, company_id: uuid.UUID, ledger_id: uuid.UUID) -> Ledger:
    ledger = db.scalar(
        select(Ledger).where(
            Ledger.id == ledger_id, Ledger.company_id == company_id
        )
    )
    if ledger is None:
        raise LedgerNotFoundError(f"{company_id}/{ledger_id}")
    return ledger


def find_ledger_by_name(
    db: Session, company_id: uuid.UUID, name: str | None, *, active_only: bool = True
) -> Ledger | None:
    """Resolve a ledger by its company-scoped, case-insensitive name."""
    if not name or not name.strip():
        return None
    query = select(Ledger).where(
        Ledger.company_id == company_id,
        func.lower(Ledger.name) == name.strip().lower(),
    )
    if active_only:
        query = query.where(Ledger.is_active.is_(True))
    return db.scalar(query.order_by(Ledger.created_at).limit(1))


def _assert_unique_name(
    db: Session,
    company_id: uuid.UUID,
    name: str,
    *,
    exclude_id: uuid.UUID | None = None,
) -> None:
    query = select(Ledger.id).where(
        Ledger.company_id == company_id,
        func.lower(Ledger.name) == name.strip().lower(),
    )
    if exclude_id is not None:
        query = query.where(Ledger.id != exclude_id)
    if db.scalar(query.limit(1)) is not None:
        raise LedgerConflictError("A ledger with this name already exists.")


def create_ledger(
    db: Session, company_id: uuid.UUID, data: LedgerCreate, *, actor: str = "local-user"
) -> Ledger:
    get_company(db, company_id)
    _assert_unique_name(db, company_id, data.name)
    ledger = Ledger(company_id=company_id, **data.model_dump())
    db.add(ledger)
    db.flush()
    record_event(
        db,
        company_id=company_id,
        entity_type=AccountingEntityType.LEDGER,
        entity_id=ledger.id,
        event_type="LEDGER_CREATED",
        actor=actor,
        new_status="ACTIVE",
        metadata={
            "name": ledger.name,
            "code": ledger.code,
            "ledger_type": ledger.ledger_type,
            "source": ledger.source.value,
        },
    )
    db.commit()
    db.refresh(ledger)
    return ledger


def update_ledger(
    db: Session, ledger: Ledger, data: LedgerUpdate, *, actor: str = "local-user"
) -> Ledger:
    payload = data.model_dump(exclude_unset=True)
    if "name" in payload and payload["name"] is not None:
        _assert_unique_name(
            db, ledger.company_id, payload["name"], exclude_id=ledger.id
        )

    for field, value in payload.items():
        old = getattr(ledger, field)
        if old != value:
            record_event(
                db,
                company_id=ledger.company_id,
                entity_type=AccountingEntityType.LEDGER,
                entity_id=ledger.id,
                event_type="LEDGER_UPDATED",
                actor=actor,
                field=field,
                old_value=old,
                new_value=value,
                previous_status="ACTIVE" if ledger.is_active else "INACTIVE",
                new_status=(
                    "ACTIVE"
                    if (value if field == "is_active" else ledger.is_active)
                    else "INACTIVE"
                ),
            )
            setattr(ledger, field, value)

    db.commit()
    db.refresh(ledger)
    return ledger


def deactivate_ledger(
    db: Session, ledger: Ledger, *, actor: str = "local-user"
) -> Ledger:
    if ledger.is_active:
        ledger.is_active = False
        record_event(
            db,
            company_id=ledger.company_id,
            entity_type=AccountingEntityType.LEDGER,
            entity_id=ledger.id,
            event_type="LEDGER_DEACTIVATED",
            actor=actor,
            previous_status="ACTIVE",
            new_status="INACTIVE",
            reason="Ledger deactivated; historical vouchers keep their name snapshot.",
        )
        db.commit()
        db.refresh(ledger)
    return ledger


__all__ = [
    "LedgerConflictError",
    "LedgerNotFoundError",
    "create_ledger",
    "deactivate_ledger",
    "find_ledger_by_name",
    "get_ledger",
    "list_ledgers",
    "update_ledger",
]
