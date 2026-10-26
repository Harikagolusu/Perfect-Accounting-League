"""Orchestration for the three client-format exports.

This module is deliberately read-only. It reads canonical records, maps them
through the template-specific mappers, validates the result and returns either
a preview or workbook bytes. Nothing is written back to the database, so
running the same export twice from the same records is idempotent.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Sequence

from sqlalchemy.orm import Session

from app.schemas.exports import ExportIssueResponse, ExportPreviewResponse
from app.services.company import get_company
from app.services.exports.accounting_voucher_export.generator import (
    generate_accounting_voucher_workbook,
)
from app.services.exports.accounting_voucher_export.mapper import (
    build_accounting_vouchers,
)
from app.services.exports.bank_export.generator import generate_bank_workbook
from app.services.exports.bank_export.mapper import build_bank_vouchers
from app.services.exports.common.models import ExportIssue
from app.services.exports.common.workbook import download_filename
from app.services.exports.invoice_export.generator import generate_invoice_workbook
from app.services.exports.invoice_export.mapper import build_invoice_rows
from app.services.invoice import get_invoice


@dataclass(frozen=True)
class ExportFile:
    content: bytes
    filename: str
    rows: int
    vouchers: int
    issues: tuple[ExportIssue, ...]

    @property
    def exceptions(self) -> tuple[ExportIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "ERROR")


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
    *,
    rows: int,
    vouchers: int,
    issues: Sequence[ExportIssue],
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


def invoice_export_preview(
    db: Session,
    company_id: uuid.UUID,
    *,
    invoice_id: uuid.UUID | None = None,
) -> ExportPreviewResponse:
    get_company(db, company_id)
    if invoice_id is not None:
        get_invoice(db, company_id, invoice_id)
    build = build_invoice_rows(
        db,
        company_id,
        invoice_ids=[invoice_id] if invoice_id is not None else None,
    )
    return _preview(
        rows=len(build.rows),
        vouchers=0,
        issues=build.issues,
    )


def invoice_export_file(
    db: Session,
    company_id: uuid.UUID,
    *,
    invoice_id: uuid.UUID | None = None,
) -> ExportFile:
    company = get_company(db, company_id)
    invoice = None
    if invoice_id is not None:
        invoice = get_invoice(db, company_id, invoice_id)
    build = build_invoice_rows(
        db,
        company_id,
        invoice_ids=[invoice_id] if invoice_id is not None else None,
    )
    label = (
        f"Invoice_{invoice.invoice_number or invoice.id}"
        if invoice is not None
        else "Invoices"
    )
    return ExportFile(
        content=generate_invoice_workbook(build.rows),
        filename=download_filename(company.name, label),
        rows=len(build.rows),
        vouchers=0,
        issues=build.issues,
    )


def bank_export_preview(
    db: Session,
    company_id: uuid.UUID,
    *,
    statement_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ExportPreviewResponse:
    get_company(db, company_id)
    build = build_bank_vouchers(
        db,
        company_id,
        statement_id=statement_id,
        date_from=date_from,
        date_to=date_to,
    )
    return _preview(
        rows=sum(len(voucher.lines) for voucher in build.vouchers),
        vouchers=len(build.vouchers),
        issues=build.issues,
    )


def bank_export_file(
    db: Session,
    company_id: uuid.UUID,
    *,
    statement_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ExportFile:
    company = get_company(db, company_id)
    build = build_bank_vouchers(
        db,
        company_id,
        statement_id=statement_id,
        date_from=date_from,
        date_to=date_to,
    )
    return ExportFile(
        content=generate_bank_workbook(build.vouchers),
        filename=download_filename(company.name, "Bank_Vouchers"),
        rows=sum(len(voucher.lines) for voucher in build.vouchers),
        vouchers=len(build.vouchers),
        issues=build.issues,
    )


def accounting_voucher_export_preview(
    db: Session,
    company_id: uuid.UUID,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    include_invoices: bool = True,
    include_bank: bool = True,
) -> ExportPreviewResponse:
    get_company(db, company_id)
    build = build_accounting_vouchers(
        db,
        company_id,
        date_from=date_from,
        date_to=date_to,
        include_invoices=include_invoices,
        include_bank=include_bank,
    )
    return _preview(
        rows=sum(len(voucher.lines) for voucher in build.vouchers),
        vouchers=len(build.vouchers),
        issues=build.issues,
    )


def accounting_voucher_export_file(
    db: Session,
    company_id: uuid.UUID,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    include_invoices: bool = True,
    include_bank: bool = True,
) -> ExportFile:
    company = get_company(db, company_id)
    build = build_accounting_vouchers(
        db,
        company_id,
        date_from=date_from,
        date_to=date_to,
        include_invoices=include_invoices,
        include_bank=include_bank,
    )
    return ExportFile(
        content=generate_accounting_voucher_workbook(build.vouchers),
        filename=download_filename(company.name, "Accounting_Vouchers"),
        rows=sum(len(voucher.lines) for voucher in build.vouchers),
        vouchers=len(build.vouchers),
        issues=build.issues,
    )


__all__ = [
    "ExportFile",
    "accounting_voucher_export_file",
    "accounting_voucher_export_preview",
    "bank_export_file",
    "bank_export_preview",
    "invoice_export_file",
    "invoice_export_preview",
]
