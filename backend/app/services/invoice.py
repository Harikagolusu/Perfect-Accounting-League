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
import time
import uuid
from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.storage import StorageError, resolve_storage_path
from app.models.document import Document, DocumentStatus
from app.models.invoice import (
    Invoice,
    InvoiceSource,
    InvoiceStatus,
    VendorMappingStatus,
)
from app.models.vendor import Vendor, VendorStatus
from app.schemas.invoice import InvoiceCreate, InvoiceUpdate
from app.services.company import get_company
from app.services.document import get_document
from app.services.invoice_extraction import (
    EXTRACTION_INTERNAL_ERROR,
    EXTRACTION_NOT_CONFIGURED,
    NOT_CONFIGURED_MESSAGE,
    ExtractionOutcome,
    ExtractionResult,
    InvoiceExtractor,
    get_extractor,
)
from app.services.invoice_validation import (
    InvoiceTransitionError,
    InvoiceValidationResult,
    assert_transition_allowed,
    validate_invoice_fields,
)
from app.services.vendor import VendorNotFoundError, get_vendor
from app.services.vendor_matching import (
    VendorMatchStatus,
    match_vendor,
    normalize_gstin,
    normalize_pan,
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

#: Columns an extraction result may populate. `company_id`, `document_id`,
#: `vendor_id`, `status` and `source` are workflow fields, never model output.
_EXTRACTED_FIELDS = (
    "invoice_number",
    "invoice_date",
    "due_date",
    "vendor_name",
    "vendor_gstin",
    "vendor_pan",
    "buyer_gstin",
    "po_number",
    "currency",
    "taxable_amount",
    "cgst_amount",
    "sgst_amount",
    "igst_amount",
    "cess_amount",
    "round_off",
    "total_amount",
)

#: Standard Tally group for supplier accounts. Only the group and the creditor
#: ledger are filled in automatically; the expense ledger stays unset until a
#: person chooses the correct accounting head.
_AUTO_VENDOR_PARENT_GROUP = "Sundry Creditors"


class InvoiceNotFoundError(Exception):
    """Raised when an invoice id does not resolve within the given company."""


class VendorOwnershipError(Exception):
    """Raised when a vendor belongs to a different company than the invoice."""


class VendorMappingConflictError(Exception):
    """Raised when a mapping action would silently undo a confirmed assignment."""


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
        # A vendor chosen on a manual invoice is an explicit human decision.
        vendor_mapping_status=(
            VendorMappingStatus.CONFIRMED
            if data.vendor_id is not None
            else VendorMappingStatus.UNMAPPED
        ),
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

    Vendor-mapping rules:
    * sending `vendor_id` is a human decision, so it becomes CONFIRMED;
    * changing the printed supplier identity invalidates an automatic match or
      pending suggestion, because it no longer describes the linked vendor;
    * a CONFIRMED vendor is never discarded by an identity edit. The person who
      confirmed it knows more than the matcher does.
    """
    payload = data.model_dump(exclude_unset=True)
    old_vendor_id = invoice.vendor_id

    if "vendor_id" in payload:
        _assert_vendor_belongs_to_company(db, invoice.company_id, payload["vendor_id"])

    identity_fields = ("vendor_name", "vendor_gstin", "vendor_pan")
    identity_changed = any(
        field in payload and payload[field] != getattr(invoice, field)
        for field in identity_fields
    )

    if "vendor_id" in payload:
        new_vendor_id = payload["vendor_id"]
        if old_vendor_id is not None and new_vendor_id != old_vendor_id:
            # Keep exactly one step of history for an explicit correction.
            invoice.previous_vendor_id = old_vendor_id
        invoice.suggested_vendor_id = None
        if new_vendor_id is None:
            # An explicit unlink is not a rejection: the invoice is simply
            # unmapped again and may be matched later.
            invoice.vendor_mapping_status = VendorMappingStatus.UNMAPPED
            invoice.vendor_match_method = None
        else:
            invoice.vendor_mapping_status = VendorMappingStatus.CONFIRMED
            invoice.vendor_match_method = None
    elif identity_changed:
        if invoice.vendor_mapping_status is VendorMappingStatus.MATCHED:
            # The automatic link described the old printed identity.
            invoice.vendor_id = None
            invoice.vendor_mapping_status = VendorMappingStatus.UNMAPPED
            invoice.vendor_match_method = None
            invoice.suggested_vendor_id = None
        elif invoice.vendor_mapping_status in {
            VendorMappingStatus.SUGGESTED,
            VendorMappingStatus.AMBIGUOUS,
            VendorMappingStatus.REJECTED,
        }:
            invoice.vendor_mapping_status = VendorMappingStatus.UNMAPPED
            invoice.vendor_match_method = None
            invoice.suggested_vendor_id = None
        # CONFIRMED mappings survive identity edits by design.

    target_status = payload.pop("status", None)
    if target_status is not None and target_status is not invoice.status:
        assert_transition_allowed(invoice.status, target_status)
        if (
            target_status is InvoiceStatus.PROCESSED
            and invoice.vendor_mapping_status
            in {VendorMappingStatus.SUGGESTED, VendorMappingStatus.AMBIGUOUS}
        ):
            # A pending suggestion or an ambiguous match is the review the
            # invoice was sent back for. Approving the data would silently
            # bypass it and leave PROCESSED next to an unconfirmed mapping.
            raise InvoiceTransitionError(
                "Confirm or reject the vendor suggestion before approving this invoice."
            )
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


def _create_vendor_from_invoice(db: Session, invoice: Invoice) -> Vendor | None:
    """Record the supplier identity printed on an unmatched invoice.

    The vendor master starts from facts the document actually contains: the
    printed name and valid GSTIN/PAN. The creditor ledger and the standard
    "Sundry Creditors" group are safe Tally defaults; the expense ledger is
    deliberately left empty because the invoice does not say which accounting
    head applies. The record is created as NEEDS_MAPPING so that gap stays
    visible in the Vendor Master.

    The caller links the result as a suggestion, never as a confirmed mapping.
    """
    name = (invoice.vendor_name or "").strip()
    if not name:
        return None

    vendor = Vendor(
        company_id=invoice.company_id,
        name=name,
        gstin=normalize_gstin(invoice.vendor_gstin),
        pan=normalize_pan(invoice.vendor_pan),
        creditor_ledger=name,
        parent_group=_AUTO_VENDOR_PARENT_GROUP,
        status=VendorStatus.NEEDS_MAPPING,
    )
    db.add(vendor)
    db.flush()
    logger.info(
        "Vendor auto-created from invoice: company=%s invoice=%s vendor=%s",
        invoice.company_id,
        invoice.id,
        vendor.id,
    )
    return vendor


def _apply_vendor_mapping(db: Session, invoice: Invoice) -> bool:
    """Match the invoice's printed supplier identity to a vendor master record.

    Returns whether the result requires a human review. The invoice is mutated
    but not committed; the extraction lifecycle commits it with the final
    status, so an extraction and its mapping cannot land half-applied.

    When no vendor matches, a vendor master record is created from the printed
    identity and linked as a suggestion. Existing assignments are never
    overwritten. A REJECTED invoice is never silently re-matched on a retry.
    """
    if invoice.vendor_id is not None:
        # A vendor is already assigned. Only a person may change it.
        if invoice.vendor_mapping_status in {
            VendorMappingStatus.UNMAPPED,
            VendorMappingStatus.SUGGESTED,
            VendorMappingStatus.AMBIGUOUS,
        }:
            invoice.vendor_mapping_status = VendorMappingStatus.CONFIRMED
        invoice.suggested_vendor_id = None
        return False

    if invoice.vendor_mapping_status is VendorMappingStatus.REJECTED:
        # The user said no. Reprocessing must not quietly say yes.
        invoice.suggested_vendor_id = None
        invoice.vendor_match_method = None
        return False

    if invoice.vendor_mapping_status is VendorMappingStatus.CONFIRMED:
        # The linked vendor was removed (ON DELETE SET NULL). There is no
        # confirmed identity left, so fall through and evaluate afresh.
        invoice.vendor_mapping_status = VendorMappingStatus.UNMAPPED
        invoice.vendor_match_method = None

    if invoice.suggested_vendor_id is not None:
        # An unconfirmed suggestion stays a suggestion on a retry. Reprocessing
        # must not turn it into an automatic assignment.
        return True

    result = match_vendor(
        db,
        invoice.company_id,
        invoice.vendor_name,
        invoice.vendor_gstin,
        invoice.vendor_pan,
    )

    if result.status is VendorMatchStatus.MATCHED:
        assert result.vendor is not None
        if result.requires_confirmation:
            invoice.vendor_id = None
            invoice.suggested_vendor_id = result.vendor.id
            invoice.vendor_mapping_status = VendorMappingStatus.SUGGESTED
            invoice.vendor_match_method = result.method
            requires_review = True
        else:
            invoice.vendor_id = result.vendor.id
            invoice.suggested_vendor_id = None
            invoice.vendor_mapping_status = VendorMappingStatus.MATCHED
            invoice.vendor_match_method = result.method
            requires_review = False
    elif result.status is VendorMatchStatus.AMBIGUOUS:
        invoice.vendor_id = None
        invoice.suggested_vendor_id = None
        invoice.vendor_mapping_status = VendorMappingStatus.AMBIGUOUS
        invoice.vendor_match_method = None
        requires_review = True
    else:
        vendor = _create_vendor_from_invoice(db, invoice)
        invoice.vendor_id = None
        if vendor is not None:
            invoice.suggested_vendor_id = vendor.id
            invoice.vendor_mapping_status = VendorMappingStatus.SUGGESTED
            # No historical match produced this suggestion, so there is no
            # match method to claim. The UI labels it as auto-created.
            invoice.vendor_match_method = None
            requires_review = True
        else:
            invoice.suggested_vendor_id = None
            invoice.vendor_mapping_status = VendorMappingStatus.UNMAPPED
            invoice.vendor_match_method = None
            requires_review = False

    logger.info(
        "Vendor mapping finished: company=%s invoice=%s status=%s method=%s reason=%s",
        invoice.company_id,
        invoice.id,
        invoice.vendor_mapping_status.value,
        invoice.vendor_match_method.value if invoice.vendor_match_method else None,
        result.reason,
    )
    return requires_review


def confirm_vendor_mapping(
    db: Session, invoice: Invoice, vendor_id: uuid.UUID | None
) -> Invoice:
    """Confirm a suggested vendor, assign one manually, or reject a suggestion.

    `vendor_id=None` rejects the current suggestion. It refuses to unassign an
    existing vendor: that would be a destructive edit disguised as a review
    action. An explicit `PATCH vendor_id: null` remains the way to unlink.

    The vendor is resolved through the company-scoped Vendor service, so an id
    from another company is indistinguishable from a missing one (404).

    Confirming a suggestion on an invoice that was already marked PROCESSED
    reopens it for data approval. That combination means the mapping review was
    bypassed earlier; the assigned ledger changes the accounting, so a person
    must approve the data again.
    """
    was_pending_review = invoice.vendor_mapping_status in {
        VendorMappingStatus.SUGGESTED,
        VendorMappingStatus.AMBIGUOUS,
    }
    if vendor_id is None:
        if invoice.vendor_id is not None:
            raise VendorMappingConflictError(
                "A vendor is already assigned to this invoice. "
                "Choose a vendor to change the mapping."
            )
        invoice.vendor_mapping_status = VendorMappingStatus.REJECTED
        invoice.vendor_match_method = None
        invoice.suggested_vendor_id = None
    else:
        vendor = get_vendor(db, invoice.company_id, vendor_id)
        was_suggestion = invoice.suggested_vendor_id == vendor.id
        if invoice.vendor_id is not None and invoice.vendor_id != vendor.id:
            # A re-mapping is an explicit correction; keep the prior assignment.
            invoice.previous_vendor_id = invoice.vendor_id
        invoice.vendor_id = vendor.id
        invoice.suggested_vendor_id = None
        invoice.vendor_mapping_status = VendorMappingStatus.CONFIRMED
        if not was_suggestion:
            invoice.vendor_match_method = None
        if was_pending_review and invoice.status is InvoiceStatus.PROCESSED:
            # Repair the state a premature approval could leave behind. The
            # transition is deliberate: this is the explicit reopen that the
            # lifecycle otherwise keeps out of normal edits.
            invoice.status = InvoiceStatus.NEEDS_REVIEW

    db.commit()
    db.refresh(invoice)
    logger.info(
        "Vendor mapping confirmed: company=%s invoice=%s status=%s vendor=%s",
        invoice.company_id,
        invoice.id,
        invoice.vendor_mapping_status.value,
        invoice.vendor_id,
    )
    return invoice


def process_invoice(
    db: Session,
    invoice: Invoice,
    *,
    document: Document | None = None,
    extractor: InvoiceExtractor | None = None,
) -> tuple[Invoice, ExtractionResult]:
    """Run extraction for one existing invoice and apply its lifecycle.

    The invoice is moved to PROCESSING before the AI call so a crash cannot
    leave it silently stuck in DRAFT with no record of the attempt. Every exit
    path then lands on PROCESSED, NEEDS_REVIEW or FAILED — never PROCESSING.

    `document` and `extractor` are injectable for tests and for the
    process-document path, which has already resolved them.
    """
    extractor = extractor or get_extractor()
    if document is None:
        document = get_document(db, invoice.company_id, invoice.document_id)

    if invoice.status is InvoiceStatus.PROCESSED:
        return invoice, ExtractionResult(
            configured=extractor.is_configured(),
            outcome=ExtractionOutcome.PROCESSED,
            reason="ALREADY_PROCESSED",
            message="This invoice is already processed.",
        )

    if not extractor.is_configured():
        # Nothing ran, so nothing changes. Marking FAILED would misreport it.
        return invoice, ExtractionResult(
            configured=False,
            outcome=ExtractionOutcome.NOT_CONFIGURED,
            reason=EXTRACTION_NOT_CONFIGURED,
            message=NOT_CONFIGURED_MESSAGE,
        )

    previous_invoice_status = invoice.status
    previous_document_status = document.status
    assert_transition_allowed(invoice.status, InvoiceStatus.PROCESSING)

    invoice.status = InvoiceStatus.PROCESSING
    document.status = DocumentStatus.PROCESSING
    db.commit()

    started = time.perf_counter()
    try:
        raw = resolve_storage_path(document.file_path).read_bytes()
    except (OSError, StorageError):
        logger.warning(
            "Stored file could not be read: company=%s document=%s",
            invoice.company_id,
            document.id,
        )
        return _finish(
            db,
            invoice,
            document,
            ExtractionResult(
                configured=True,
                outcome=ExtractionOutcome.FAILED,
                reason="DOCUMENT_UNREADABLE",
                message="The stored document could not be read.",
            ),
        )

    try:
        result = extractor.extract(document, raw)
    except Exception as exc:  # defensive: the extractor already maps known errors
        logger.error(
            "Unexpected extraction failure (%s): company=%s document=%s invoice=%s",
            type(exc).__name__,
            invoice.company_id,
            document.id,
            invoice.id,
        )
        result = ExtractionResult(
            configured=True,
            outcome=ExtractionOutcome.FAILED,
            reason=EXTRACTION_INTERNAL_ERROR,
            message="Automatic extraction failed.",
        )

    if result.outcome is ExtractionOutcome.NOT_CONFIGURED:
        # Configuration disappeared after the pre-check. Restore the prior
        # state instead of inventing a failure.
        invoice.status = previous_invoice_status
        document.status = previous_document_status
        db.commit()
        db.refresh(invoice)
        return invoice, result

    mapping_review_required = False
    try:
        if result.outcome is ExtractionOutcome.PROCESSED:
            if result.values:
                _apply_extracted_values(invoice, result.values)
            invoice.source = InvoiceSource.AI
            mapping_review_required = _apply_vendor_mapping(db, invoice)

        if result.outcome is ExtractionOutcome.NEEDS_REVIEW and result.is_invoice:
            # Apply whatever was read; the reviewer sees the values and issues.
            if result.values:
                _apply_extracted_values(invoice, result.values)
            invoice.source = InvoiceSource.AI
            mapping_review_required = _apply_vendor_mapping(db, invoice)
    except Exception as exc:
        # A persistence or mapping failure must still leave a terminal state.
        # Roll back the partial application (the PROCESSING status was already
        # committed) and let `_finish` record a safe failure. Log only the
        # exception class: a database error can carry SQL values or paths.
        db.rollback()
        logger.error(
            "Could not apply extraction result (%s): company=%s document=%s invoice=%s",
            type(exc).__name__,
            invoice.company_id,
            document.id,
            invoice.id,
        )
        result = ExtractionResult(
            configured=True,
            outcome=ExtractionOutcome.FAILED,
            reason=EXTRACTION_INTERNAL_ERROR,
            message="Automatic extraction could not be applied.",
            provider=result.provider,
            model=result.model,
            extraction_method=result.extraction_method,
        )
        mapping_review_required = False

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "Invoice extraction finished: company=%s document=%s invoice=%s "
        "outcome=%s reason=%s provider=%s model=%s method=%s elapsed_ms=%d",
        invoice.company_id,
        document.id,
        invoice.id,
        result.outcome.value,
        result.reason,
        result.provider,
        result.model,
        result.extraction_method,
        elapsed_ms,
    )
    return _finish(
        db,
        invoice,
        document,
        result,
        force_review=mapping_review_required,
    )


def _finish(
    db: Session,
    invoice: Invoice,
    document: Document,
    result: ExtractionResult,
    *,
    force_review: bool = False,
) -> tuple[Invoice, ExtractionResult]:
    """Persist the final statuses for one extraction attempt.

    `force_review` lets vendor mapping escalate a clean extraction: a pending
    name suggestion or an ambiguous match is not a safe accounting assignment,
    so a person must look at it even though the document was read correctly.
    """
    if result.outcome is ExtractionOutcome.PROCESSED:
        if force_review:
            invoice.status = InvoiceStatus.NEEDS_REVIEW
            # Keep the API result consistent with the stored invoice: a
            # mapping that needs a person is not a fully processed invoice.
            result = replace(
                result,
                outcome=ExtractionOutcome.NEEDS_REVIEW,
                reason="VENDOR_MAPPING_REVIEW",
                message="Invoice extracted. Review the vendor mapping before approving.",
            )
        else:
            invoice.status = InvoiceStatus.PROCESSED
        document.status = DocumentStatus.PROCESSED
    elif result.outcome is ExtractionOutcome.NEEDS_REVIEW:
        invoice.status = InvoiceStatus.NEEDS_REVIEW
        # Processing itself completed; a human now judges the result.
        document.status = DocumentStatus.PROCESSED
    else:
        invoice.status = InvoiceStatus.FAILED
        document.status = DocumentStatus.FAILED

    db.commit()
    db.refresh(invoice)
    db.refresh(document)
    return invoice, result


def _apply_extracted_values(invoice: Invoice, values: dict[str, object]) -> None:
    """Write extracted non-null values onto the invoice.

    A null from the model never clears an existing value: a retry cannot wipe
    a field a person already filled in, and missing data stays missing.
    """
    for field in _EXTRACTED_FIELDS:
        value = values.get(field)
        if value is not None:
            setattr(invoice, field, value)


def _find_extraction_invoice(
    db: Session, company_id: uuid.UUID, document_id: uuid.UUID
) -> Invoice | None:
    """The most recent AI-created invoice for this document, if any.

    Reusing it is what keeps repeated process-document calls from creating a
    second invoice for the same file. A manually entered invoice is never
    hijacked: it has a different source.
    """
    return db.scalar(
        select(Invoice)
        .where(
            Invoice.company_id == company_id,
            Invoice.document_id == document_id,
            Invoice.source == InvoiceSource.AI,
        )
        .order_by(Invoice.created_at.desc())
        .limit(1)
    )


def process_document(
    db: Session, company_id: uuid.UUID, document_id: uuid.UUID
) -> tuple[Document, ExtractionResult, Invoice | None]:
    """Attempt automatic extraction for a document.

    Returns the document, the extraction result and the invoice the result was
    applied to. When no provider is configured nothing is created or changed.
    """
    document = get_document(db, company_id, document_id)
    extractor = get_extractor()

    if not extractor.is_configured():
        logger.info(
            "Extraction not configured; document %s left as %s",
            document_id,
            document.status.value,
        )
        return (
            document,
            ExtractionResult(
                configured=False,
                outcome=ExtractionOutcome.NOT_CONFIGURED,
                reason=EXTRACTION_NOT_CONFIGURED,
                message=NOT_CONFIGURED_MESSAGE,
            ),
            None,
        )

    invoice = _find_extraction_invoice(db, company_id, document_id)
    if invoice is None:
        invoice = Invoice(
            company_id=company_id,
            document_id=document_id,
            status=InvoiceStatus.DRAFT,
            source=InvoiceSource.AI,
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)

    if invoice.status is InvoiceStatus.PROCESSED:
        return (
            document,
            ExtractionResult(
                configured=True,
                outcome=ExtractionOutcome.PROCESSED,
                reason="ALREADY_PROCESSED",
                message="This document already produced a processed invoice.",
            ),
            invoice,
        )

    invoice, result = process_invoice(
        db, invoice, document=document, extractor=extractor
    )
    return document, result, invoice


__all__ = [
    "InvoiceNotFoundError",
    "VendorMappingConflictError",
    "VendorOwnershipError",
    "confirm_vendor_mapping",
    "create_invoice",
    "delete_invoice",
    "get_invoice",
    "list_invoices",
    "process_document",
    "process_invoice",
    "update_invoice",
    "validate_fields",
]
