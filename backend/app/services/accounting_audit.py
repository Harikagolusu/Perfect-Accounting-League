"""Append-only accounting audit trail.

Every accounting decision is recorded here: ledger changes, voucher edits,
approval/rejection, export and posting verification. Events are never updated
or deleted by the application.

The audit payload is deliberately limited to business values. API keys, raw
provider responses and filesystem paths are not accepted by this module.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.voucher import (
    AccountingAuditEvent,
    AccountingEntityType,
    Voucher,
)


def record_event(
    db: Session,
    *,
    company_id: uuid.UUID,
    entity_type: AccountingEntityType,
    entity_id: uuid.UUID | None,
    event_type: str,
    actor: str = "local-user",
    voucher_id: uuid.UUID | None = None,
    previous_status: str | None = None,
    new_status: str | None = None,
    field: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AccountingAuditEvent:
    """Add one audit event to the current transaction.

    The caller commits. This keeps an event and the accounting change it
    describes in the same database transaction.
    """
    event = AccountingAuditEvent(
        company_id=company_id,
        entity_type=entity_type,
        entity_id=entity_id,
        voucher_id=voucher_id,
        event_type=event_type,
        actor=actor or "local-user",
        previous_status=previous_status,
        new_status=new_status,
        field=field,
        old_value=_value(old_value),
        new_value=_value(new_value),
        reason=reason,
        event_metadata=metadata,
    )
    db.add(event)
    return event


def _value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    # Keep the audit row bounded; full line snapshots live in metadata.
    return text[:4000]


def list_events(
    db: Session,
    company_id: uuid.UUID,
    *,
    voucher_id: uuid.UUID | None = None,
    entity_type: AccountingEntityType | None = None,
    limit: int = 500,
) -> list[AccountingAuditEvent]:
    """Return events newest first, always scoped to the company."""
    query = select(AccountingAuditEvent).where(
        AccountingAuditEvent.company_id == company_id
    )
    if voucher_id is not None:
        query = query.where(AccountingAuditEvent.voucher_id == voucher_id)
    if entity_type is not None:
        query = query.where(AccountingAuditEvent.entity_type == entity_type)
    query = query.order_by(
        AccountingAuditEvent.created_at.desc(), AccountingAuditEvent.id
    ).limit(max(1, min(limit, 2000)))
    return list(db.scalars(query).all())


def list_voucher_events(
    db: Session, company_id: uuid.UUID, voucher_id: uuid.UUID
) -> list[AccountingAuditEvent]:
    voucher = db.scalar(
        select(Voucher).where(
            Voucher.id == voucher_id, Voucher.company_id == company_id
        )
    )
    if voucher is None:
        return []
    return list_events(db, company_id, voucher_id=voucher_id)


__all__ = ["list_events", "list_voucher_events", "record_event"]
