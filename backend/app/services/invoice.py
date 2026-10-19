"""Invoice business logic — the Invoice Processing foundation.

All database access for the Invoice entity lives here; routers stay thin.

Company scoping
---------------
Every read filters on `company_id`, and there is no lookup by invoice id
alone. Cross-company access raises `InvoiceNotFoundError`, which the router
turns into a 404 exactly like a missing record.

Referential isolation
---------------------
An invoice may only point at a document and a vendor belonging to the *same*
company. A vendor id from another company is rejected rather than silently
accepted, because an invoice linked across companies would corrupt every
per-company report built later.

Deletion
--------
Invoices are hard deleted, unlike Company and Vendor which are deactivated.
An invoice is derived working data with no soft-delete state of its own, and
the original document — the artifact of record — is never touched.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.invoice import Invoice, InvoiceSource, InvoiceStatus
from app.models.vendor import Vendor
from app.schemas.invoice import InvoiceCreate, InvoiceUpdate
from app.services.company import get_company
from app.services.document import get_document
from app.services.invoice_extraction import (
    ExtractionResult,
    get_extractor,
)
from app.services.invoice_validation import (
    InvoiceValidationResult,
    assert_transition_allowed,
    validate_invoice_fields,
)

logger = logging.getLogger(__name__)

_VALIDATED_FIELDS = (
    "invoice_number",
    "invoice_date",
    "due_date",
    "vendor_gstin",
    "vendor_pan",
    "buyer_gstin",
    "taxable_amount",
    "cgst_amount",
    "sgst_amount",
    "igst_amount",
    "cess_amount",
    "round_off",
    "total_amount",
)


class InvoiceNotFoundError(Exception):
    """Raised when an invoice id does not resolve within the given company."""


class VendorOwnershipError(Exception):
    """Raised when a vendor belongs to a different company than the invoice."""


def list_invoices(
    db: Session,
    company_id: uuid.UUID,
    *,
    status: InvoiceStatus | None = None,
    vendor_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
) -> list[Invoice]:
    """Return a company's invoices, newest first, with optional filters."""
    stmt = (
        select(Invoice)
        .where(Invoice.company_id == company_id)
        .order_by(Invoice.created_at.desc(), Invoice.invoice_number)
    )
    if status is not None:
        stmt = stmt.where(Invoice.status == status)
    if vendor_id is not None:
        stmt = stmt.where(Invoice.vendor_id == vendor_id)
    if document_id is not None:
        stmt = stmt.where(Invoice.document_id == document_id)
    return list(db.scalars(stmt).all())


def get_invoice(db: Session, company_id: uuid.UUID, invoice_id: uuid.UUID) -> Invoice:
    """Return an invoice only if it belongs to `company_id`."""
    invoice = db.scalar(
        select(Invoice).where(
            Invoice.id == invoice_id, Invoice.company_id == company_id
        )
    )
    if invoice is None:
        raise InvoiceNotFoundError(f"{company_id}/{invoice_id}")
    return invoice


def _assert_vendor_belongs_to_company(
    db: Session, company_id: uuid.UUID, vendor_id: uuid.UUID | None
) -> None:
    """Confirm the vendor exists and belongs to this company.

    A vendor from another company is refused rather than quietly unlinked, and
    the error carries the same 404 shape as "not found" so a caller cannot
    probe for the existence of ids in other companies.
    """
    if vendor_id is None:
        return
    vendor = db.get(Vendor, vendor_id)
    if vendor is None or vendor.company_id != company_id:
        raise VendorOwnershipError(str(vendor_id))


def validate_fields(values: dict[str, object]) -> InvoiceValidationResult:
    """Run the shared structural checks against a set of invoice values."""
    typed: dict[str, object] = {key: values.get(key) for key in _VALIDATED_FIELDS}
    return validate_invoice_fields(**typed)  # type: ignore[arg-type]


def create_invoice(db: Session, company_id: uuid.UUID, data: InvoiceCreate) -> Invoice:
    """Create an invoice against an existing document of this company.

    Raises CompanyNotFoundError, DocumentNotFoundError or VendorOwnershipError
    rather than letting a foreign-key violation surface as a 500.
    """
    get_company(db, company_id)
    # The document must belong to this company, not merely exist.
    get_document(db, company_id, data.document_id)
    _assert_vendor_belongs_to_company(db, company_id, data.vendor_id)

    values = data.model_dump()
    issues = validate_fields(values)

    stored = {
        key: value
        for key, value in values.items()
        if key not in {"document_id", "vendor_id", "status"}
    }
    invoice = Invoice(
        company_id=company_id,
        document_id=data.document_id,
        vendor_id=data.vendor_id,
        # A new invoice is a draft. Status is reached through the documented
        # lifecycle, never assigned freely at creation.
        status=InvoiceStatus.DRAFT,
        # Entered by a person, and nothing has read the file. The enum has no
        # value that would imply otherwise.
        source=InvoiceSource.MANUAL,
        **stored,
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    if issues.issues:
        # Recorded for the reviewer; the values themselves are untouched.
        logger.info(
            "Invoice %s created with %d validation issue(s): %s",
            invoice.id,
            len(issues.issues),
            "; ".join(issues.issues),
        )
    logger.info(
        "Invoice created: company=%s invoice=%s document=%s",
        company_id,
        invoice.id,
        invoice.document_id,
    )
    return invoice


def update_invoice(db: Session, invoice: Invoice, data: InvoiceUpdate) -> Invoice:
    """Apply only the fields the client actually sent.

    A status change is accepted only when the lifecycle allows it.
    """
    payload = data.model_dump(exclude_unset=True)

    if "vendor_id" in payload:
        _assert_vendor_belongs_to_company(db, invoice.company_id, payload["vendor_id"])

    target_status = payload.pop("status", None)
    if target_status is not None and target_status is not invoice.status:
        assert_transition_allowed(invoice.status, target_status)
        invoice.status = target_status

    for field, value in payload.items():
        setattr(invoice, field, value)

    db.commit()
    db.refresh(invoice)
    return invoice


def delete_invoice(db: Session, company_id: uuid.UUID, invoice_id: uuid.UUID) -> Invoice:
    """Delete an invoice. Its source document is deliberately left in place."""
    invoice = get_invoice(db, company_id, invoice_id)
    document_id = invoice.document_id
    db.delete(invoice)
    db.commit()
    logger.info(
        "Invoice deleted: company=%s invoice=%s (document %s retained)",
        company_id,
        invoice_id,
        document_id,
    )
    return invoice


def process_document(
    db: Session, company_id: uuid.UUID, document_id: uuid.UUID
) -> tuple[Document, ExtractionResult]:
    """Attempt automatic extraction for a document.

    No extraction engine is configured yet, so this says so plainly and
    changes nothing: the document keeps its status and no invoice value is
    invented. When a real engine is added, this is the one function that needs
    to change to drive the lifecycle.
    """
    document = get_document(db, company_id, document_id)
    extractor = get_extractor()

    if not extractor.is_configured():
        # No status change on purpose: nothing was processed, so marking the
        # document PROCESSING or FAILED would misreport what happened.
        logger.info(
            "Extraction not configured; document %s left as %s",
            document_id,
            document.status.value,
        )
        return document, extractor.extract(document, b"")

    from app.core.storage import resolve_storage_path

    raw = resolve_storage_path(document.file_path).read_bytes()
    result = extractor.extract(document, raw)
    logger.info(
        "Extraction attempted for document %s: configured=%s",
        document_id,
        result.configured,
    )
    return document, result


__all__ = [
    "InvoiceNotFoundError",
    "VendorOwnershipError",
    "create_invoice",
    "delete_invoice",
    "get_invoice",
    "list_invoices",
    "process_document",
    "update_invoice",
    "validate_fields",
]