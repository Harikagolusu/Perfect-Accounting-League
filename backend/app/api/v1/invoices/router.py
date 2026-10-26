"""Invoice Processing API module."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.invoice import Invoice, InvoiceStatus
from app.schemas.exports import ExportPreviewResponse
from app.schemas.invoice import (
    InvoiceCreate,
    InvoiceResponse,
    InvoiceUpdate,
    ProcessInvoiceResponse,
    VendorMappingRequest,
    VendorMatchCandidateResponse,
    VendorMatchResponse,
)
from app.schemas.invoice_line_item import (
    InvoiceLineItemCreate,
    InvoiceLineItemResponse,
    InvoiceLineItemUpdate,
)
from app.schemas.voucher import LedgerSuggestionResponse
from app.services.company import CompanyNotFoundError
from app.services.ledger_suggestion import suggest_for_invoice
from app.services.exports.common.workbook import content_disposition
from app.services.exports.service import (
    invoice_export_file,
    invoice_export_preview,
)
from app.services.document import DocumentNotFoundError
from app.services.invoice import (
    InvoiceNotFoundError,
    VendorMappingConflictError,
    VendorOwnershipError,
    confirm_vendor_mapping,
    create_invoice,
    delete_invoice,
    get_invoice,
    list_invoices,
    process_document,
    process_invoice as run_invoice_extraction,
    update_invoice,
)
from app.services.invoice_line_item import (
    InvoiceLineItemConflictError,
    InvoiceLineItemNotFoundError,
    create_line_item,
    delete_line_item,
    list_line_items,
    update_line_item,
)
from app.services.invoice_validation import InvoiceTransitionError
from app.services.vendor import VendorNotFoundError
from app.services.vendor_matching import match_vendor

router = APIRouter()


def _process_response(
    result,  # ExtractionResult; kept untyped here to avoid a schema import cycle
    *,
    document_id: UUID,
    invoice_id: UUID | None = None,
) -> ProcessInvoiceResponse:
    """Map the service result onto the safe API shape."""
    return ProcessInvoiceResponse(
        document_id=document_id,
        invoice_id=invoice_id,
        configured=result.configured,
        status=result.outcome.value,
        reason=result.reason,
        message=result.message,
        issues=list(result.issues),
        is_invoice=result.is_invoice,
        provider=result.provider,
        model=result.model,
        usage=result.usage,
        extraction_method=result.extraction_method,
    )


def invoice_or_404(
    company_id: UUID = Path(description="Owning company id."),
    invoice_id: UUID = Path(description="Invoice id, scoped to this company."),
    db: Session = Depends(get_db),
) -> Invoice:
    """Resolve the invoice within the company, or 404."""
    try:
        return get_invoice(db, company_id, invoice_id)
    except InvoiceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found"
        ) from None


@router.post(
    "",
    response_model=InvoiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an invoice from a document",
)
def post_invoice(
    payload: InvoiceCreate,
    company_id: UUID = Path(description="Company the invoice belongs to."),
    db: Session = Depends(get_db),
) -> InvoiceResponse:
    try:
        return create_invoice(db, company_id, payload)
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        ) from None
    except DocumentNotFoundError:
        # A document of another company is indistinguishable from a missing one.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        ) from None
    except VendorOwnershipError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Vendor not found"
        ) from None


@router.get("", response_model=list[InvoiceResponse], summary="List a company's invoices")
def get_invoices(
    company_id: UUID = Path(description="Company to scope the listing to."),
    status_filter: InvoiceStatus | None = Query(
        default=None, alias="status", description="Filter by invoice status."
    ),
    vendor_id: UUID | None = Query(default=None, description="Filter by vendor."),
    document_id: UUID | None = Query(
        default=None, description="Filter by source document."
    ),
    db: Session = Depends(get_db),
) -> list[InvoiceResponse]:
    return list_invoices(
        db,
        company_id,
        status=status_filter,
        vendor_id=vendor_id,
        document_id=document_id,
    )


@router.get(
    "/export/preview",
    response_model=ExportPreviewResponse,
    summary="Preview the client invoice-sheet export",
)
def get_invoice_export_preview(
    company_id: UUID = Path(description="Owning company id."),
    invoice_id: UUID | None = Query(
        default=None,
        description="Export one invoice; omit to export the company's invoices.",
    ),
    db: Session = Depends(get_db),
) -> ExportPreviewResponse:
    try:
        return invoice_export_preview(db, company_id, invoice_id=invoice_id)
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        ) from None
    except InvoiceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found"
        ) from None


@router.get(
    "/export.xlsx",
    summary="Download the client invoice-sheet workbook",
)
def get_invoice_export_workbook(
    company_id: UUID = Path(description="Owning company id."),
    invoice_id: UUID | None = Query(
        default=None,
        description="Export one invoice; omit to export the company's invoices.",
    ),
    db: Session = Depends(get_db),
) -> Response:
    try:
        result = invoice_export_file(db, company_id, invoice_id=invoice_id)
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        ) from None
    except InvoiceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found"
        ) from None
    return Response(
        content=result.content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": content_disposition(result.filename)},
    )


@router.get("/{invoice_id}", response_model=InvoiceResponse, summary="Get an invoice")
def get_invoice_by_id(invoice: Invoice = Depends(invoice_or_404)) -> InvoiceResponse:
    return invoice


@router.get(
    "/{invoice_id}/vendor-match",
    response_model=VendorMatchResponse,
    summary="Preview the deterministic vendor match",
)
def get_vendor_match(
    invoice: Invoice = Depends(invoice_or_404),
    db: Session = Depends(get_db),
) -> VendorMatchResponse:
    """Return the current match and its candidates without changing anything.

    Ambiguous invoices use this to show the real candidate vendors. The preview
    is company-scoped through the invoice and never writes to the database.
    """
    result = match_vendor(
        db,
        invoice.company_id,
        invoice.vendor_name,
        invoice.vendor_gstin,
        invoice.vendor_pan,
    )
    return VendorMatchResponse(
        status=result.status.value,
        reason=result.reason,
        method=result.method,
        vendor_id=result.vendor.id if result.vendor else None,
        requires_confirmation=result.requires_confirmation,
        candidates=[
            VendorMatchCandidateResponse(
                id=candidate.id,
                name=candidate.name,
                gstin=candidate.gstin,
                pan=candidate.pan,
                parent_group=candidate.parent_group,
                default_expense_ledger=candidate.default_expense_ledger,
                creditor_ledger=candidate.creditor_ledger,
            )
            for candidate in result.candidates
        ],
    )


@router.get(
    "/{invoice_id}/line-items",
    response_model=list[InvoiceLineItemResponse],
    summary="List an invoice's line items",
)
def get_line_items(
    invoice: Invoice = Depends(invoice_or_404),
    db: Session = Depends(get_db),
) -> list[InvoiceLineItemResponse]:
    return list_line_items(db, invoice.company_id, invoice.id)


@router.post(
    "/{invoice_id}/line-items",
    response_model=InvoiceLineItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a line item to an invoice",
)
def post_line_item(
    payload: InvoiceLineItemCreate,
    invoice: Invoice = Depends(invoice_or_404),
    db: Session = Depends(get_db),
) -> InvoiceLineItemResponse:
    try:
        return create_line_item(db, invoice.company_id, invoice.id, payload)
    except InvoiceLineItemConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from None


@router.patch(
    "/{invoice_id}/line-items/{line_item_id}",
    response_model=InvoiceLineItemResponse,
    summary="Update an invoice line item",
)
def patch_line_item(
    payload: InvoiceLineItemUpdate,
    line_item_id: UUID = Path(description="Line item id, scoped to this invoice."),
    invoice: Invoice = Depends(invoice_or_404),
    db: Session = Depends(get_db),
) -> InvoiceLineItemResponse:
    try:
        return update_line_item(
            db, invoice.company_id, invoice.id, line_item_id, payload
        )
    except InvoiceLineItemNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Line item not found"
        ) from None
    except InvoiceLineItemConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from None


@router.delete(
    "/{invoice_id}/line-items/{line_item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an invoice line item",
)
def remove_line_item(
    line_item_id: UUID = Path(description="Line item id, scoped to this invoice."),
    invoice: Invoice = Depends(invoice_or_404),
    db: Session = Depends(get_db),
) -> Response:
    try:
        delete_line_item(db, invoice.company_id, invoice.id, line_item_id)
    except InvoiceLineItemNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Line item not found"
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/{invoice_id}", response_model=InvoiceResponse, summary="Update an invoice"
)
def patch_invoice(
    payload: InvoiceUpdate,
    invoice: Invoice = Depends(invoice_or_404),
    db: Session = Depends(get_db),
) -> InvoiceResponse:
    try:
        return update_invoice(db, invoice, payload)
    except VendorOwnershipError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Vendor not found"
        ) from None
    except InvoiceTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None


@router.post(
    "/{invoice_id}/vendor-mapping",
    response_model=InvoiceResponse,
    summary="Confirm, assign or reject an invoice's vendor mapping",
)
def post_vendor_mapping(
    payload: VendorMappingRequest,
    invoice: Invoice = Depends(invoice_or_404),
    db: Session = Depends(get_db),
) -> InvoiceResponse:
    """Record a human vendor-mapping decision.

    A vendor id is verified against this company before assignment. Sending
    null rejects the current suggestion and never unassigns an existing vendor.
    """
    try:
        return confirm_vendor_mapping(db, invoice, payload.vendor_id)
    except VendorNotFoundError:
        # A vendor of another company is indistinguishable from a missing one.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Vendor not found"
        ) from None
    except VendorMappingConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from None


@router.get(
    "/{invoice_id}/ledger-suggestion",
    response_model=LedgerSuggestionResponse,
    summary="Explainable ledger suggestions for an invoice",
)
def get_invoice_ledger_suggestion(
    invoice: Invoice = Depends(invoice_or_404),
    db: Session = Depends(get_db),
) -> LedgerSuggestionResponse:
    return suggest_for_invoice(db, invoice.company_id, invoice.id)


@router.delete(
    "/{invoice_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an invoice",
)
def remove_invoice(
    invoice: Invoice = Depends(invoice_or_404), db: Session = Depends(get_db)
) -> Response:
    """Deletes the invoice only. The source document stays in the inbox."""
    delete_invoice(db, invoice.company_id, invoice.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/process-document/{document_id}",
    response_model=ProcessInvoiceResponse,
    summary="Attempt automatic extraction for a document",
)
def process_document_route(
    document_id: UUID = Path(description="Document to attempt extraction on."),
    company_id: UUID = Path(description="Owning company id."),
    db: Session = Depends(get_db),
) -> ProcessInvoiceResponse:
    """Try to read an invoice out of a stored document.

    When no AI provider is configured the result says so plainly and nothing
    changes. When one is configured, the document's invoice is created or
    reused and moved through the existing invoice lifecycle.
    """
    try:
        document, result, invoice = process_document(db, company_id, document_id)
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        ) from None
    return _process_response(
        result,
        document_id=document.id,
        invoice_id=invoice.id if invoice is not None else None,
    )


@router.post(
    "/{invoice_id}/process",
    response_model=ProcessInvoiceResponse,
    summary="Attempt automatic extraction for an invoice's document",
)
def process_invoice(
    invoice: Invoice = Depends(invoice_or_404), db: Session = Depends(get_db)
) -> ProcessInvoiceResponse:
    """Run extraction for this invoice's source document."""
    updated, result = run_invoice_extraction(db, invoice)
    return _process_response(
        result,
        document_id=updated.document_id,
        invoice_id=updated.id,
    )
