"""Invoice line-item business logic.

Line items are company-scoped through their invoice. There is no lookup by line
item id alone: every read requires `company_id`, `invoice_id` and the line id,
so a line from another company is indistinguishable from a missing one.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.invoice_line_item import InvoiceLineItem
from app.schemas.invoice_line_item import (
    InvoiceLineItemCreate,
    InvoiceLineItemUpdate,
)
from app.services.invoice import get_invoice


class InvoiceLineItemNotFoundError(Exception):
    """Raised when a line item does not resolve within its invoice/company."""


class InvoiceLineItemConflictError(Exception):
    """Raised when a line number is missing or already used."""


def list_line_items(
    db: Session, company_id: uuid.UUID, invoice_id: uuid.UUID
) -> list[InvoiceLineItem]:
    """Return an invoice's line items in their explicit order."""
    get_invoice(db, company_id, invoice_id)
    return list(
        db.scalars(
            select(InvoiceLineItem)
            .where(
                InvoiceLineItem.company_id == company_id,
                InvoiceLineItem.invoice_id == invoice_id,
            )
            .order_by(InvoiceLineItem.line_number)
        ).all()
    )


def get_line_item(
    db: Session,
    company_id: uuid.UUID,
    invoice_id: uuid.UUID,
    line_item_id: uuid.UUID,
) -> InvoiceLineItem:
    """Return a line item only if it belongs to this company and invoice."""
    item = db.scalar(
        select(InvoiceLineItem).where(
            InvoiceLineItem.id == line_item_id,
            InvoiceLineItem.company_id == company_id,
            InvoiceLineItem.invoice_id == invoice_id,
        )
    )
    if item is None:
        raise InvoiceLineItemNotFoundError(
            f"{company_id}/{invoice_id}/{line_item_id}"
        )
    return item


def _next_line_number(
    db: Session, company_id: uuid.UUID, invoice_id: uuid.UUID
) -> int:
    current = db.scalar(
        select(func.max(InvoiceLineItem.line_number)).where(
            InvoiceLineItem.company_id == company_id,
            InvoiceLineItem.invoice_id == invoice_id,
        )
    )
    return int(current or 0) + 1


def _assert_line_number_available(
    db: Session,
    company_id: uuid.UUID,
    invoice_id: uuid.UUID,
    line_number: int,
    *,
    exclude_id: uuid.UUID | None = None,
) -> None:
    conditions = [
        InvoiceLineItem.company_id == company_id,
        InvoiceLineItem.invoice_id == invoice_id,
        InvoiceLineItem.line_number == line_number,
    ]
    if exclude_id is not None:
        conditions.append(InvoiceLineItem.id != exclude_id)
    existing = db.scalar(select(InvoiceLineItem).where(*conditions))
    if existing is not None:
        raise InvoiceLineItemConflictError(
            f"Line number {line_number} is already used on this invoice."
        )


def create_line_item(
    db: Session,
    company_id: uuid.UUID,
    invoice_id: uuid.UUID,
    data: InvoiceLineItemCreate,
) -> InvoiceLineItem:
    """Create a line item against an invoice of this company."""
    invoice = get_invoice(db, company_id, invoice_id)
    payload = data.model_dump()
    line_number = payload.pop("line_number") or _next_line_number(
        db, company_id, invoice_id
    )
    _assert_line_number_available(db, company_id, invoice_id, line_number)

    item = InvoiceLineItem(
        company_id=invoice.company_id,
        invoice_id=invoice.id,
        line_number=line_number,
        **payload,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def update_line_item(
    db: Session,
    company_id: uuid.UUID,
    invoice_id: uuid.UUID,
    line_item_id: uuid.UUID,
    data: InvoiceLineItemUpdate,
) -> InvoiceLineItem:
    """Apply only the fields the client actually sent."""
    item = get_line_item(db, company_id, invoice_id, line_item_id)
    payload = data.model_dump(exclude_unset=True)

    if "line_number" in payload:
        if payload["line_number"] is None:
            raise InvoiceLineItemConflictError("Line number is required.")
        _assert_line_number_available(
            db,
            company_id,
            invoice_id,
            payload["line_number"],
            exclude_id=item.id,
        )
    if "description" in payload and payload["description"] is None:
        raise InvoiceLineItemConflictError("Description is required.")

    for field, value in payload.items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return item


def delete_line_item(
    db: Session,
    company_id: uuid.UUID,
    invoice_id: uuid.UUID,
    line_item_id: uuid.UUID,
) -> InvoiceLineItem:
    item = get_line_item(db, company_id, invoice_id, line_item_id)
    db.delete(item)
    db.commit()
    return item


__all__ = [
    "InvoiceLineItemConflictError",
    "InvoiceLineItemNotFoundError",
    "create_line_item",
    "delete_line_item",
    "get_line_item",
    "list_line_items",
    "update_line_item",
]
