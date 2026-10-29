"""Export approved PAL vouchers as the client's accounting workbook.

The existing `accounting_voucher_export` path remains untouched for backwards
compatibility. This module adds the Phase 1 lifecycle-aware path:

    APPROVED -> workbook generated -> AWAITING_IMPORT_CONFIRMATION

It never sets `POSTED`. Only a verified external posting reference can do that.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bank import BankAccount, BankStatement, BankTransaction
from app.models.invoice import Invoice, InvoiceDirection
from app.models.vendor import Vendor
from app.models.voucher import Voucher as VoucherModel
from app.models.voucher import VoucherSide, VoucherStatus, VoucherType
from app.schemas.exports import ExportIssueResponse, ExportPreviewResponse
from app.services.company import get_company
from app.services.exports.accounting_voucher_export.generator import (
    generate_accounting_voucher_workbook,
)
from app.services.exports.common.models import (
    ExportIssue,
    Voucher as ExportVoucher,
    VoucherLine as ExportVoucherLine,
)
from app.services.exports.common.workbook import download_filename
from app.services.exports.service import ExportFile
from app.services.voucher import list_vouchers, mark_vouchers_exported
from app.services.posting_period import resolve_tally_posting_date
from app.services.voucher_validation import validate_voucher

_VOUCHER_TYPE_LABEL = {
    VoucherType.PURCHASE: "Purchase",
    VoucherType.SALES: "Sales",
    VoucherType.RECEIPT: "Receipt",
    VoucherType.PAYMENT: "Payment",
    VoucherType.CONTRA: "Contra",
    VoucherType.JOURNAL: "Journal",
}

_EXPORTABLE_STATUSES = (
    VoucherStatus.APPROVED,
    VoucherStatus.AWAITING_IMPORT_CONFIRMATION,
)


def _issue_response(issue: ExportIssue) -> ExportIssueResponse:
    return ExportIssueResponse(
        code=issue.code,
        message=issue.message,
        severity=issue.severity,
        record_type=issue.record_type,
        record_id=issue.record_id,
        field=issue.field,
    )


def _preview(
    *, rows: int, vouchers: int, issues: Sequence[ExportIssue]
) -> ExportPreviewResponse:
    errors = [issue for issue in issues if issue.severity == "ERROR"]
    warnings = [issue for issue in issues if issue.severity == "WARNING"]
    excluded = {
        issue.record_id for issue in errors if issue.record_id is not None
    }
    return ExportPreviewResponse(
        rows=rows,
        vouchers=vouchers,
        excluded_records=len(excluded),
        exceptions=[_issue_response(issue) for issue in errors],
        warnings=[_issue_response(issue) for issue in warnings],
    )


def _party_details(
    db: Session, voucher: VoucherModel
) -> tuple[dict[str, str | None], str | None]:
    """Return party address/pincode/GSTIN and original invoice number."""
    empty: dict[str, str | None] = {
        "address": None,
        "pincode": None,
        "gstin": None,
    }
    if voucher.source_id is None:
        return empty, None
    if voucher.source_type.value == "INVOICE":
        invoice = db.scalar(
            select(Invoice).where(
                Invoice.id == voucher.source_id,
                Invoice.company_id == voucher.company_id,
            )
        )
        if invoice is None:
            return empty, None
        vendor = (
            db.scalar(
                select(Vendor).where(
                    Vendor.id == invoice.vendor_id,
                    Vendor.company_id == voucher.company_id,
                )
            )
            if invoice.vendor_id is not None
            else None
        )
        return (
            {
                "address": (vendor.address if vendor else None),
                "pincode": (vendor.pincode if vendor else None),
                "gstin": invoice.vendor_gstin or (vendor.gstin if vendor else None),
            },
            invoice.invoice_number,
        )
    if voucher.source_type.value == "BANK_TRANSACTION":
        transaction = db.scalar(
            select(BankTransaction).where(
                BankTransaction.id == voucher.source_id,
                BankTransaction.company_id == voucher.company_id,
            )
        )
        if transaction is None:
            return empty, None
        return (
            {
                "address": transaction.counterparty_address,
                "pincode": transaction.counterparty_pincode,
                "gstin": transaction.counterparty_gstin,
            },
            None,
        )
    return empty, None


def _party_side(voucher: VoucherModel) -> VoucherSide:
    if voucher.voucher_type is VoucherType.SALES:
        return VoucherSide.DR
    if voucher.voucher_type in (VoucherType.PURCHASE, VoucherType.RECEIPT):
        return VoucherSide.CR
    if voucher.voucher_type is VoucherType.PAYMENT:
        return VoucherSide.DR
    return VoucherSide.CR


def _to_export_voucher(
    db: Session, voucher: VoucherModel, posting_month: date | None = None
) -> tuple[ExportVoucher | None, list[ExportIssue]]:
    issues: list[ExportIssue] = []
    validation = validate_voucher(db, voucher)
    if validation:
        issues.extend(
            ExportIssue(
                code=issue.code,
                message=issue.message,
                severity="ERROR",
                record_type="VOUCHER",
                record_id=str(voucher.id),
                field=issue.field,
            )
            for issue in validation
        )
        return None, issues

    party, original_invoice_no = _party_details(db, voucher)
    party_side = _party_side(voucher)
    lines: list[ExportVoucherLine] = []
    for line in voucher.lines:
        is_party = line.side is party_side and line.line_type != "TAX"
        lines.append(
            ExportVoucherLine(
                ledger_name=line.ledger_name,
                amount=line.amount,
                side=line.side.value,
                party_address=party["address"] if is_party else None,
                party_pincode=party["pincode"] if is_party else None,
                party_gstin=party["gstin"] if is_party else None,
                item_name=line.item_name,
                billed_quantity=line.billed_quantity,
                item_rate=line.item_rate,
                item_rate_per=line.item_rate_per,
                item_amount=line.item_amount,
                ledger_narration=line.narration,
            )
        )

    suffix = voucher.voucher_number.rsplit("-", 1)[-1]
    number = int(suffix) if suffix.isdigit() else 0
    source_type = (
        "INVOICE"
        if voucher.source_type.value == "INVOICE"
        else (
            "BANK_TRANSACTION"
            if voucher.source_type.value == "BANK_TRANSACTION"
            else "MANUAL"
        )
    )
    export = ExportVoucher(
        voucher_date=resolve_tally_posting_date(voucher.voucher_date, posting_month),
        voucher_type=_VOUCHER_TYPE_LABEL[voucher.voucher_type],
        change_mode=(
            "Accounting Invoice"
            if voucher.voucher_type in (VoucherType.PURCHASE, VoucherType.SALES)
            else "Accounting"
        ),
        source_type=source_type,  # type: ignore[arg-type]
        source_id=str(voucher.id),
        lines=tuple(lines),
        narration=voucher.narration,
        original_invoice_no=original_invoice_no,
        voucher_number=number,
    )
    return export, issues


def _select_vouchers(
    db: Session,
    company_id: uuid.UUID,
    *,
    voucher_ids: Sequence[uuid.UUID] | None,
    date_from: date | None,
    date_to: date | None,
) -> list[VoucherModel]:
    vouchers = list_vouchers(
        db,
        company_id,
        date_from=date_from,
        date_to=date_to,
    )
    allowed = set(voucher_ids) if voucher_ids else None
    return [
        voucher
        for voucher in vouchers
        if voucher.status in _EXPORTABLE_STATUSES
        and (allowed is None or voucher.id in allowed)
    ]


def voucher_export_preview(
    db: Session,
    company_id: uuid.UUID,
    *,
    voucher_ids: Sequence[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    posting_month: date | None = None,
) -> ExportPreviewResponse:
    get_company(db, company_id)
    selected = _select_vouchers(
        db,
        company_id,
        voucher_ids=voucher_ids,
        date_from=date_from,
        date_to=date_to,
    )
    exports: list[ExportVoucher] = []
    issues: list[ExportIssue] = []
    for voucher in selected:
        export, voucher_issues = _to_export_voucher(db, voucher, posting_month)
        issues.extend(voucher_issues)
        if export is not None:
            exports.append(export)
    return _preview(
        rows=sum(len(voucher.lines) for voucher in exports),
        vouchers=len(exports),
        issues=issues,
    )


def voucher_export_file(
    db: Session,
    company_id: uuid.UUID,
    *,
    voucher_ids: Sequence[uuid.UUID] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    posting_month: date | None = None,
    actor: str = "local-user",
) -> ExportFile:
    company = get_company(db, company_id)
    selected = _select_vouchers(
        db,
        company_id,
        voucher_ids=voucher_ids,
        date_from=date_from,
        date_to=date_to,
    )
    exports: list[ExportVoucher] = []
    included: list[VoucherModel] = []
    issues: list[ExportIssue] = []
    for voucher in selected:
        export, voucher_issues = _to_export_voucher(db, voucher, posting_month)
        issues.extend(voucher_issues)
        if export is not None:
            exports.append(export)
            included.append(voucher)

    content = generate_accounting_voucher_workbook(tuple(exports))
    filename = download_filename(company.name, "Approved_Accounting_Vouchers")
    if included:
        mark_vouchers_exported(
            db,
            included,
            actor=actor,
            filename=filename,
        )
    return ExportFile(
        content=content,
        filename=filename,
        rows=sum(len(voucher.lines) for voucher in exports),
        vouchers=len(exports),
        issues=tuple(issues),
    )


__all__ = ["voucher_export_file", "voucher_export_preview"]
