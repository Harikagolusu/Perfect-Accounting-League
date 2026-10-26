"""Traceable accounting-mapping business logic."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounting_mapping import (
    AccountingMapping,
    AccountingMappingMatchType,
    AccountingMappingSourceType,
)
from app.models.ledger import Ledger
from app.models.vendor import Vendor
from app.models.voucher import AccountingEntityType
from app.schemas.accounting_mapping import (
    AccountingMappingCreate,
    AccountingMappingUpdate,
)
from app.services.accounting_audit import record_event
from app.services.company import get_company
from app.services.ledger import get_ledger


class AccountingMappingNotFoundError(Exception):
    """Raised when a mapping id does not resolve within the company."""


class AccountingMappingConflictError(Exception):
    """Raised for a duplicate mapping or an invalid evidence reference."""


def list_mappings(
    db: Session,
    company_id: uuid.UUID,
    *,
    source_type: AccountingMappingSourceType | None = None,
    include_inactive: bool = False,
) -> list[AccountingMapping]:
    query = select(AccountingMapping).where(
        AccountingMapping.company_id == company_id
    )
    if source_type is not None:
        query = query.where(AccountingMapping.source_type == source_type)
    if not include_inactive:
        query = query.where(AccountingMapping.is_active.is_(True))
    query = query.order_by(
        AccountingMapping.created_at.desc(), AccountingMapping.id
    )
    return list(db.scalars(query).all())


def get_mapping(
    db: Session, company_id: uuid.UUID, mapping_id: uuid.UUID
) -> AccountingMapping:
    mapping = db.scalar(
        select(AccountingMapping).where(
            AccountingMapping.id == mapping_id,
            AccountingMapping.company_id == company_id,
        )
    )
    if mapping is None:
        raise AccountingMappingNotFoundError(f"{company_id}/{mapping_id}")
    return mapping


def _assert_evidence(
    db: Session, company_id: uuid.UUID, data: AccountingMappingCreate
) -> None:
    if data.vendor_id is not None:
        vendor = db.scalar(
            select(Vendor).where(
                Vendor.id == data.vendor_id, Vendor.company_id == company_id
            )
        )
        if vendor is None:
            raise AccountingMappingConflictError(
                "The vendor does not belong to this company."
            )
    try:
        get_ledger(db, company_id, data.ledger_id)
    except Exception as exc:  # keep service errors explicit for the router
        raise AccountingMappingConflictError(
            "The ledger does not belong to this company."
        ) from exc


def _find_duplicate(
    db: Session, company_id: uuid.UUID, data: AccountingMappingCreate
) -> AccountingMapping | None:
    query = select(AccountingMapping).where(
        AccountingMapping.company_id == company_id,
        AccountingMapping.source_type == data.source_type,
        AccountingMapping.match_type == data.match_type,
        AccountingMapping.ledger_id == data.ledger_id,
        AccountingMapping.is_active.is_(True),
    )
    if data.vendor_id is not None:
        query = query.where(AccountingMapping.vendor_id == data.vendor_id)
    if data.counterparty_name is not None:
        query = query.where(
            AccountingMapping.counterparty_name == data.counterparty_name
        )
    if data.narration_pattern is not None:
        query = query.where(
            AccountingMapping.narration_pattern == data.narration_pattern
        )
    if data.voucher_type is not None:
        query = query.where(AccountingMapping.voucher_type == data.voucher_type)
    return db.scalar(query.limit(1))


def create_mapping(
    db: Session,
    company_id: uuid.UUID,
    data: AccountingMappingCreate,
    *,
    actor: str = "local-user",
) -> AccountingMapping:
    get_company(db, company_id)
    _assert_evidence(db, company_id, data)
    duplicate = _find_duplicate(db, company_id, data)
    if duplicate is not None:
        return duplicate
    mapping = AccountingMapping(
        company_id=company_id,
        **data.model_dump(),
        approved_by=actor,
        approved_at=datetime.now(timezone.utc),
    )
    db.add(mapping)
    db.flush()
    record_event(
        db,
        company_id=company_id,
        entity_type=AccountingEntityType.MAPPING,
        entity_id=mapping.id,
        event_type="MAPPING_CREATED",
        actor=actor,
        new_status="ACTIVE",
        reason=mapping.reason,
        metadata={
            "source_type": mapping.source_type.value,
            "match_type": mapping.match_type.value,
            "ledger_id": str(mapping.ledger_id),
        },
    )
    db.commit()
    db.refresh(mapping)
    return mapping


def update_mapping(
    db: Session,
    mapping: AccountingMapping,
    data: AccountingMappingUpdate,
    *,
    actor: str = "local-user",
) -> AccountingMapping:
    payload = data.model_dump(exclude_unset=True)
    if "ledger_id" in payload and payload["ledger_id"] is None:
        raise AccountingMappingConflictError("An accounting mapping requires a ledger.")
    if "ledger_id" in payload and payload["ledger_id"] is not None:
        try:
            get_ledger(db, mapping.company_id, payload["ledger_id"])
        except Exception as exc:
            raise AccountingMappingConflictError(
                "The ledger does not belong to this company."
            ) from exc
    if "vendor_id" in payload and payload["vendor_id"] is not None:
        vendor = db.scalar(
            select(Vendor).where(
                Vendor.id == payload["vendor_id"],
                Vendor.company_id == mapping.company_id,
            )
        )
        if vendor is None:
            raise AccountingMappingConflictError(
                "The vendor does not belong to this company."
            )

    for field, value in payload.items():
        old = getattr(mapping, field)
        if old != value:
            record_event(
                db,
                company_id=mapping.company_id,
                entity_type=AccountingEntityType.MAPPING,
                entity_id=mapping.id,
                event_type="MAPPING_UPDATED",
                actor=actor,
                field=field,
                old_value=old,
                new_value=value,
            )
            setattr(mapping, field, value)

    db.commit()
    db.refresh(mapping)
    return mapping


def deactivate_mapping(
    db: Session, mapping: AccountingMapping, *, actor: str = "local-user"
) -> AccountingMapping:
    if mapping.is_active:
        mapping.is_active = False
        record_event(
            db,
            company_id=mapping.company_id,
            entity_type=AccountingEntityType.MAPPING,
            entity_id=mapping.id,
            event_type="MAPPING_DEACTIVATED",
            actor=actor,
            previous_status="ACTIVE",
            new_status="INACTIVE",
            reason="Mapping deactivated; it will not be suggested again.",
        )
        db.commit()
        db.refresh(mapping)
    return mapping


__all__ = [
    "AccountingMappingConflictError",
    "AccountingMappingNotFoundError",
    "create_mapping",
    "deactivate_mapping",
    "get_mapping",
    "list_mappings",
    "update_mapping",
]
