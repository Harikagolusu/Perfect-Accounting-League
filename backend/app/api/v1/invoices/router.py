"""Invoice Processing API module."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.invoice import Invoice, InvoiceStatus
from app.schemas.invoice import (
    InvoiceCreate,
    InvoiceResponse,
    InvoiceUpdate,
    ProcessInvoiceResponse,
)
from app.services.company import CompanyNotFoundError
from app.services.document import DocumentNotFoundError
from app.services.invoice import (
    InvoiceNotFoundError,
    VendorOwnershipError,
    create_invoice,
    delete_invoice,
    get_invoice,
    list_invoices,
    process_document,
    update_invoice,
)
from app.services.invoice_validation import InvoiceTransitionError

router = APIRouter()


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


@router.get("/{invoice_id}", response_model=InvoiceResponse, summary="Get an invoice")
def get_invoice_by_id(invoice: Invoice = Depends(invoice_or_404)) -> InvoiceResponse:
    return invoice


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

    No extraction engine is configured yet, so this reports that plainly and
    changes nothing. It never returns invented invoice values.
    """
    try:
        document, result = process_document(db, company_id, document_id)
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        ) from None
    return ProcessInvoiceResponse(
        document_id=document.id,
        configured=result.configured,
        reason=result.reason,
        message=result.message or "",
    )


@router.post(
    "/{invoice_id}/process",
    response_model=ProcessInvoiceResponse,
    summary="Attempt automatic extraction for an invoice's document",
)
def process_invoice(
    invoice: Invoice = Depends(invoice_or_404), db: Session = Depends(get_db)
) -> ProcessInvoiceResponse:
    """Same honest answer as the document route, for an invoice that exists."""
    _document, result = process_document(db, invoice.company_id, invoice.document_id)
    return ProcessInvoiceResponse(
        document_id=invoice.document_id,
        configured=result.configured,
        reason=result.reason,
        message=result.message or "",
        invoice_id=invoice.id,
    )