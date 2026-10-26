"""Canonical invoices and bank transactions -> multi-line accounting vouchers.

A purchase invoice becomes one balanced voucher:

    Supplier / creditor        Cr   total
    Expense ledger             Dr   taxable
    CGST / SGST / IGST ledger  Dr   each tax amount
    Round-off ledger           Dr/Cr the rounding difference

Bank transactions reuse the same two-line Receipt/Payment voucher builder as
the bank workbook. No missing ledger is replaced with a default name, and an
unbalanced voucher is reported rather than forced into balance.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.invoice import Invoice
from app.models.vendor import Vendor
from app.services.company import get_company
from app.services.exports.bank_export.mapper import build_bank_vouchers
from app.services.exports.common.models import (
    ExportIssue,
    Voucher,
    VoucherBuild,
    VoucherLine,
)
from app.services.exports.common.validation import (
    assign_voucher_numbers,
    error,
    voucher_balance_issues,
)

ZERO = Decimal("0")
TAX_LEDGERS = (
    ("igst_amount", "igst_ledger", "IGST"),
    ("cgst_amount", "cgst_ledger", "CGST"),
    ("sgst_amount", "sgst_ledger", "SGST"),
    ("cess_amount", "cess_ledger", "Cess"),
)


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


def _invoice_voucher(
    company: Company,
    invoice: Invoice,
    vendor: Vendor | None,
) -> tuple[Voucher | None, list[ExportIssue]]:
    record_id = str(invoice.id)
    record_type = "INVOICE"
    issues: list[ExportIssue] = []

    if not _text(invoice.invoice_number):
        issues.append(
            error(
                "INVOICE_FIELD_REQUIRED",
                "Invoice number is required to identify the voucher's original invoice.",
                record_type=record_type,
                record_id=record_id,
                field="Original Invoice No.",
            )
        )
    if invoice.invoice_date is None:
        issues.append(
            error(
                "INVOICE_FIELD_REQUIRED",
                "Invoice date is required for the voucher date.",
                record_type=record_type,
                record_id=record_id,
                field="Voucher Date",
            )
        )

    if invoice.vendor_id is None or vendor is None:
        issues.append(
            error(
                "VENDOR_MAPPING_REQUIRED",
                "A vendor master record is required before this invoice can be posted.",
                record_type=record_type,
                record_id=record_id,
            )
        )
        return None, issues

    creditor_ledger = _text(vendor.creditor_ledger)
    if not creditor_ledger:
        issues.append(
            error(
                "LEDGER_MAPPING_REQUIRED",
                "The vendor has no creditor ledger mapping.",
                record_type=record_type,
                record_id=record_id,
                field="Ledger Name",
            )
        )

    expense_ledger = _text(vendor.default_expense_ledger)
    if not expense_ledger:
        issues.append(
            error(
                "LEDGER_MAPPING_REQUIRED",
                "The vendor has no default expense ledger mapping.",
                record_type=record_type,
                record_id=record_id,
                field="Ledger Name",
            )
        )

    taxable = invoice.taxable_amount
    if taxable is None:
        issues.append(
            error(
                "INVOICE_FIELD_REQUIRED",
                "Taxable amount is required to build the voucher.",
                record_type=record_type,
                record_id=record_id,
                field="Ledger Amount",
            )
        )

    total = invoice.total_amount
    if total is None:
        issues.append(
            error(
                "INVOICE_FIELD_REQUIRED",
                "Invoice total is required to build the creditor ledger row.",
                record_type=record_type,
                record_id=record_id,
                field="Ledger Amount",
            )
        )

    tax_values: list[tuple[Decimal, str, str]] = []
    for amount_attr, ledger_attr, label in TAX_LEDGERS:
        amount = getattr(invoice, amount_attr)
        if amount is None or amount == ZERO:
            continue
        if amount < ZERO:
            issues.append(
                error(
                    "LEDGER_AMOUNT_REQUIRED",
                    f"{label} amount must not be negative.",
                    record_type=record_type,
                    record_id=record_id,
                    field="Ledger Amount",
                )
            )
            continue
        ledger = _text(getattr(company, ledger_attr))
        if not ledger:
            issues.append(
                error(
                    "LEDGER_MAPPING_REQUIRED",
                    f"{label} ledger is not configured for this company.",
                    record_type=record_type,
                    record_id=record_id,
                    field="Ledger Name",
                )
            )
            continue
        tax_values.append((amount, ledger, label))

    round_off = invoice.round_off or ZERO
    round_off_ledger = _text(company.round_off_ledger)
    if round_off != ZERO and not round_off_ledger:
        issues.append(
            error(
                "LEDGER_MAPPING_REQUIRED",
                "The round-off ledger is not configured for this company.",
                record_type=record_type,
                record_id=record_id,
                field="Ledger Name",
            )
        )

    if any(issue.severity == "ERROR" for issue in issues):
        return None, issues

    if total <= ZERO:
        issues.append(
            error(
                "INVOICE_FIELD_REQUIRED",
                "A positive invoice total is required to build the voucher.",
                record_type=record_type,
                record_id=record_id,
                field="Ledger Amount",
            )
        )
        return None, issues
    if taxable <= ZERO:
        issues.append(
            error(
                "INVOICE_FIELD_REQUIRED",
                "A positive taxable amount is required to build the voucher.",
                record_type=record_type,
                record_id=record_id,
                field="Ledger Amount",
            )
        )
        return None, issues

    lines: list[VoucherLine] = [
        VoucherLine(
            ledger_name=creditor_ledger,
            amount=total,
            side="Cr",
            party_address=_text(vendor.address),
            party_pincode=_text(vendor.pincode),
            party_gstin=_text(invoice.vendor_gstin) or _text(vendor.gstin),
        ),
        VoucherLine(
            ledger_name=expense_ledger,
            amount=taxable,
            side="Dr",
        ),
    ]
    for amount, ledger, _label in tax_values:
        lines.append(VoucherLine(ledger_name=ledger, amount=amount, side="Dr"))

    if round_off > ZERO:
        lines.append(
            VoucherLine(
                ledger_name=round_off_ledger,
                amount=round_off,
                side="Dr",
            )
        )
    elif round_off < ZERO:
        lines.append(
            VoucherLine(
                ledger_name=round_off_ledger,
                amount=abs(round_off),
                side="Cr",
            )
        )

    voucher = Voucher(
        voucher_date=invoice.invoice_date,
        voucher_type="Purchase",
        change_mode="Accounting Invoice",
        source_type="INVOICE",
        source_id=record_id,
        lines=tuple(lines),
        original_invoice_no=_text(invoice.invoice_number),
    )
    balance_issues = voucher_balance_issues(voucher)
    if balance_issues:
        return None, issues + balance_issues
    return voucher, issues


def build_accounting_vouchers(
    db: Session,
    company_id: uuid.UUID,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    include_invoices: bool = True,
    include_bank: bool = True,
) -> VoucherBuild:
    """Build one workbook's worth of vouchers from the selected sources."""
    company = get_company(db, company_id)
    vouchers: list[Voucher] = []
    issues: list[ExportIssue] = []

    if include_bank:
        bank_build = build_bank_vouchers(
            db,
            company_id,
            date_from=date_from,
            date_to=date_to,
        )
        issues.extend(bank_build.issues)
        vouchers.extend(
            replace(voucher, voucher_number=0)
            for voucher in bank_build.vouchers
        )

    if include_invoices:
        query = select(Invoice).where(Invoice.company_id == company_id)
        if date_from is not None:
            query = query.where(Invoice.invoice_date >= date_from)
        if date_to is not None:
            query = query.where(Invoice.invoice_date <= date_to)
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
        for invoice in invoices:
            voucher, invoice_issues = _invoice_voucher(
                company, invoice, vendors.get(invoice.vendor_id)
            )
            issues.extend(invoice_issues)
            if voucher is not None:
                vouchers.append(voucher)

    return VoucherBuild(
        vouchers=assign_voucher_numbers(vouchers),
        issues=tuple(issues),
    )


__all__ = ["build_accounting_vouchers"]
