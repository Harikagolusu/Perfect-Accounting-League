"""Canonical invoices -> the client's 18-column invoice sheet.

Every value is either copied from a canonical field or reported as an issue.
No tax rate, HSN code, quantity, address, place of supply or line total is
invented. A row with an error is left out of the workbook; a warning is shown
in the preview but does not block the row.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.invoice import Invoice
from app.models.invoice_line_item import InvoiceLineItem
from app.models.vendor import Vendor
from app.services.company import get_company
from app.services.exports.common.models import ExportBuild, ExportIssue
from app.services.exports.common.validation import error, warning
from app.services.exports.invoice_export.schemas import (
    SUPPLY_TYPE_LABELS,
    InvoiceExportRow,
)

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def _pan_from_gstin(gstin: str | None) -> str | None:
    """Read the PAN embedded in a valid GSTIN.

    This is a structural fact about a GSTIN, not a guess about a vendor.
    """
    if not gstin or len(gstin) != 15:
        return None
    candidate = gstin[2:12]
    return candidate if PAN_RE.match(candidate) else None


def _text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _load_vendors(
    db: Session, company_id: uuid.UUID, invoices: list[Invoice]
) -> dict[uuid.UUID, Vendor]:
    vendor_ids = {invoice.vendor_id for invoice in invoices if invoice.vendor_id}
    if not vendor_ids:
        return {}
    return {
        vendor.id: vendor
        for vendor in db.scalars(
            select(Vendor).where(
                Vendor.company_id == company_id,
                Vendor.id.in_(vendor_ids),
            )
        ).all()
    }


def _load_line_items(
    db: Session, company_id: uuid.UUID, invoices: list[Invoice]
) -> dict[uuid.UUID, list[InvoiceLineItem]]:
    invoice_ids = [invoice.id for invoice in invoices]
    if not invoice_ids:
        return {}
    grouped: dict[uuid.UUID, list[InvoiceLineItem]] = {}
    for item in db.scalars(
        select(InvoiceLineItem)
        .where(
            InvoiceLineItem.company_id == company_id,
            InvoiceLineItem.invoice_id.in_(invoice_ids),
        )
        .order_by(InvoiceLineItem.invoice_id, InvoiceLineItem.line_number)
    ).all():
        grouped.setdefault(item.invoice_id, []).append(item)
    return grouped


def _header(
    invoice: Invoice, vendor: Vendor | None
) -> tuple[dict[str, object] | None, list[ExportIssue]]:
    record_id = str(invoice.id)
    issues: list[ExportIssue] = []

    if not _text(invoice.invoice_number):
        issues.append(
            error(
                "INVOICE_FIELD_REQUIRED",
                "Invoice number is required for the client invoice export.",
                record_type="INVOICE",
                record_id=record_id,
                field="Inv Number",
            )
        )
    if invoice.invoice_date is None:
        issues.append(
            error(
                "INVOICE_FIELD_REQUIRED",
                "Invoice date is required for the client invoice export.",
                record_type="INVOICE",
                record_id=record_id,
                field="Inv Date",
            )
        )

    if invoice.vendor_id is None or vendor is None:
        issues.append(
            error(
                "VENDOR_MAPPING_REQUIRED",
                "A vendor master record is required before this invoice can be exported.",
                record_type="INVOICE",
                record_id=record_id,
            )
        )

    name = _text(invoice.vendor_name) or _text(vendor.name if vendor else None)
    if not name:
        issues.append(
            error(
                "INVOICE_FIELD_REQUIRED",
                "Supplier name is required for the client invoice export.",
                record_type="INVOICE",
                record_id=record_id,
                field="Name",
            )
        )

    gstin = _text(invoice.vendor_gstin) or _text(vendor.gstin if vendor else None)
    if not gstin:
        issues.append(
            error(
                "INVOICE_FIELD_REQUIRED",
                "Supplier GST registration number is required for the client invoice export.",
                record_type="INVOICE",
                record_id=record_id,
                field="GST Reg No",
            )
        )

    place_of_supply = _text(invoice.place_of_supply)
    if not place_of_supply:
        issues.append(
            error(
                "INVOICE_FIELD_REQUIRED",
                "Place of supply is required for the client invoice export.",
                record_type="INVOICE",
                record_id=record_id,
                field="Place of Supply",
            )
        )

    pan = _text(invoice.vendor_pan) or _text(vendor.pan if vendor else None)
    pan = pan or _pan_from_gstin(gstin)
    if not pan:
        issues.append(
            warning(
                "FIELD_NOT_AVAILABLE",
                "Supplier PAN is not available; the column is left blank.",
                record_type="INVOICE",
                record_id=record_id,
                field="PAN",
            )
        )

    address = _text(vendor.address if vendor else None)
    if not address:
        issues.append(
            warning(
                "FIELD_NOT_AVAILABLE",
                "Vendor address is not available; the column is left blank.",
                record_type="INVOICE",
                record_id=record_id,
                field="Address",
            )
        )

    msme = _text(vendor.msme_registration if vendor else None)
    if not msme:
        issues.append(
            warning(
                "FIELD_NOT_AVAILABLE",
                "MSME registration is not available; the column is left blank.",
                record_type="INVOICE",
                record_id=record_id,
                field="MSME Reg. No",
            )
        )

    if any(issue.severity == "ERROR" for issue in issues):
        return None, issues

    return (
        {
            "invoice_date": invoice.invoice_date,
            "invoice_number": _text(invoice.invoice_number),
            "name": name,
            "msme_registration": msme,
            "gstin": gstin,
            "pan": pan,
            "place_of_supply": place_of_supply,
            "address": address,
        },
        issues,
    )


def _resolve_tax(
    line: InvoiceLineItem,
    invoice: Invoice,
    single_line: bool,
    attr: str,
) -> Decimal | None:
    line_value = getattr(line, attr)
    if line_value is not None:
        return line_value
    return getattr(invoice, attr) if single_line else None


def build_invoice_rows(
    db: Session,
    company_id: uuid.UUID,
    *,
    invoice_ids: list[uuid.UUID] | None = None,
) -> ExportBuild:
    """Build one client row per invoice line, or report why a line is missing."""
    get_company(db, company_id)
    query = select(Invoice).where(Invoice.company_id == company_id)
    if invoice_ids is not None:
        query = query.where(Invoice.id.in_(invoice_ids))
    invoices = list(
        db.scalars(
            query.order_by(
                Invoice.invoice_date,
                Invoice.invoice_number,
                Invoice.created_at,
                Invoice.id,
            )
        ).all()
    )

    vendors = _load_vendors(db, company_id, invoices)
    line_items = _load_line_items(db, company_id, invoices)
    rows: list[InvoiceExportRow] = []
    issues: list[ExportIssue] = []

    for invoice in invoices:
        header, header_issues = _header(invoice, vendors.get(invoice.vendor_id))
        issues.extend(header_issues)
        if header is None:
            continue

        lines = line_items.get(invoice.id, [])
        if not lines:
            issues.append(
                error(
                    "INVOICE_LINE_ITEMS_REQUIRED",
                    "At least one invoice line item is required for the client invoice export.",
                    record_type="INVOICE",
                    record_id=str(invoice.id),
                    field="Item Discription",
                )
            )
            continue

        single_line = len(lines) == 1
        invoice_taxes = (invoice.igst_amount, invoice.cgst_amount, invoice.sgst_amount)
        for line in lines:
            line_issues: list[ExportIssue] = []
            record_id = str(invoice.id)

            description = _text(line.description)
            if not description:
                line_issues.append(
                    error(
                        "INVOICE_FIELD_REQUIRED",
                        "Item description is required for the client invoice export.",
                        record_type="INVOICE",
                        record_id=record_id,
                        field="Item Discription",
                    )
                )

            supply_label = SUPPLY_TYPE_LABELS.get((line.supply_type or "").upper())
            if supply_label is None:
                line_issues.append(
                    error(
                        "OUTPUT_MAPPING_REQUIRED",
                        "Service/Goods must be GOODS or SERVICE for the client invoice export.",
                        record_type="INVOICE",
                        record_id=record_id,
                        field="Service/Goods",
                    )
                )

            hsn = _text(line.hsn_sac)
            if not hsn:
                line_issues.append(
                    error(
                        "INVOICE_FIELD_REQUIRED",
                        "HSN/SAC is required for the client invoice export.",
                        record_type="INVOICE",
                        record_id=record_id,
                        field="HSN",
                    )
                )

            if line.quantity is None:
                line_issues.append(
                    error(
                        "INVOICE_FIELD_REQUIRED",
                        "Quantity is required for the client invoice export.",
                        record_type="INVOICE",
                        record_id=record_id,
                        field="Quantity",
                    )
                )

            if line.gst_rate is None:
                line_issues.append(
                    error(
                        "TAX_DATA_REQUIRED",
                        "GST rate is required for the client invoice export and is never inferred.",
                        record_type="INVOICE",
                        record_id=record_id,
                        field="GST Rate",
                    )
                )

            taxable = (
                line.taxable_amount
                if line.taxable_amount is not None
                else (invoice.taxable_amount if single_line else None)
            )
            if taxable is None:
                line_issues.append(
                    error(
                        "INVOICE_FIELD_REQUIRED",
                        "Taxable amount is required for this line.",
                        record_type="INVOICE",
                        record_id=record_id,
                        field="Taxable Amount",
                    )
                )

            total = (
                line.total_amount
                if line.total_amount is not None
                else (invoice.total_amount if single_line else None)
            )
            if total is None:
                line_issues.append(
                    error(
                        "INVOICE_FIELD_REQUIRED",
                        "Invoice amount is required for this line.",
                        record_type="INVOICE",
                        record_id=record_id,
                        field="Invoice Amount",
                    )
                )

            igst = _resolve_tax(line, invoice, single_line, "igst_amount")
            cgst = _resolve_tax(line, invoice, single_line, "cgst_amount")
            sgst = _resolve_tax(line, invoice, single_line, "sgst_amount")

            if (
                not single_line
                and all(value is None for value in (igst, cgst, sgst))
                and any(value is not None for value in invoice_taxes)
            ):
                line_issues.append(
                    warning(
                        "TAX_ALLOCATION_REQUIRED",
                        (
                            "Invoice-level tax amounts exist but this line has no "
                            "tax allocation; tax columns are left blank."
                        ),
                        record_type="INVOICE",
                        record_id=record_id,
                        field="GST Rate",
                    )
                )

            issues.extend(line_issues)
            if any(issue.severity == "ERROR" for issue in line_issues):
                continue

            rows.append(
                InvoiceExportRow(
                    invoice_date=header["invoice_date"],
                    invoice_number=header["invoice_number"],
                    name=header["name"],
                    msme_registration=header["msme_registration"],
                    gstin=header["gstin"],
                    pan=header["pan"],
                    place_of_supply=header["place_of_supply"],
                    address=header["address"],
                    item_description=description,
                    supply_type=supply_label,
                    hsn_sac=hsn,
                    quantity=line.quantity,
                    gst_rate=line.gst_rate,
                    taxable_amount=taxable,
                    igst_amount=igst,
                    cgst_amount=cgst,
                    sgst_amount=sgst,
                    invoice_amount=total,
                )
            )

    return ExportBuild(rows=tuple(rows), issues=tuple(issues), voucher_count=0)


__all__ = ["build_invoice_rows"]
