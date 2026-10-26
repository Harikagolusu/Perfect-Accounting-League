"""Draft-voucher business logic.

This module owns the accounting state machine. It never posts to Tally/Zoho and
never treats a generated workbook as a posting. Approval, export and verified
posting are separate, explicit transitions.

The service boundary accepts an `actor` display name. Phase 1 has no user table,
so no role or identity is fabricated; a future auth layer can replace the
router's default without changing the domain rules.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.accounting_mapping import (
    AccountingMapping,
    AccountingMappingMatchType,
    AccountingMappingSourceType,
)
from app.models.bank import BankAccount, BankStatement, BankTransaction
from app.models.company import Company
from app.models.invoice import Invoice, InvoiceDirection
from app.models.voucher import (
    AccountingEntityType,
    Voucher,
    VoucherLine,
    VoucherSide,
    VoucherSourceType,
    VoucherStatus,
    VoucherType,
)
from app.schemas.voucher import (
    VoucherCreate,
    VoucherIssueResponse,
    VoucherLineInput,
    VoucherUpdate,
)
from app.services.accounting_audit import record_event
from app.services.company import get_company
from app.services.ledger import find_ledger_by_name, get_ledger
from app.services.voucher_validation import (
    VoucherIssue,
    VoucherTransitionError,
    VoucherValidationError,
    assert_transition_allowed,
    issues_as_dicts,
    validate_voucher,
)

ZERO = Decimal("0")

_NUMBER_PREFIX = {
    VoucherType.PURCHASE: "PUR",
    VoucherType.SALES: "SAL",
    VoucherType.RECEIPT: "RCT",
    VoucherType.PAYMENT: "PAY",
    VoucherType.CONTRA: "CNT",
    VoucherType.JOURNAL: "JRN",
}


class VoucherNotFoundError(Exception):
    """Raised when a voucher id does not resolve within the company."""


def list_vouchers(
    db: Session,
    company_id: uuid.UUID,
    *,
    status: VoucherStatus | None = None,
    voucher_type: VoucherType | None = None,
    source_type: VoucherSourceType | None = None,
    source_id: uuid.UUID | None = None,
    date_from: Any = None,
    date_to: Any = None,
) -> list[Voucher]:
    query = select(Voucher).where(Voucher.company_id == company_id)
    if status is not None:
        query = query.where(Voucher.status == status)
    if voucher_type is not None:
        query = query.where(Voucher.voucher_type == voucher_type)
    if source_type is not None:
        query = query.where(Voucher.source_type == source_type)
    if source_id is not None:
        query = query.where(Voucher.source_id == source_id)
    if date_from is not None:
        query = query.where(Voucher.voucher_date >= date_from)
    if date_to is not None:
        query = query.where(Voucher.voucher_date <= date_to)
    query = query.order_by(
        Voucher.voucher_date.desc(), Voucher.created_at.desc(), Voucher.id
    )
    return list(db.scalars(query).all())


def get_voucher(
    db: Session, company_id: uuid.UUID, voucher_id: uuid.UUID
) -> Voucher:
    voucher = db.scalar(
        select(Voucher).where(
            Voucher.id == voucher_id, Voucher.company_id == company_id
        )
    )
    if voucher is None:
        raise VoucherNotFoundError(f"{company_id}/{voucher_id}")
    return voucher


def next_voucher_number(
    db: Session, company_id: uuid.UUID, voucher_type: VoucherType
) -> str:
    """Return the next controlled number for a company and voucher type.

    Numbers restart per voucher type, matching the client's workbook examples.
    The database unique constraint is the final guard against a duplicate.
    """
    prefix = _NUMBER_PREFIX[voucher_type]
    numbers = db.scalars(
        select(Voucher.voucher_number).where(
            Voucher.company_id == company_id,
            Voucher.voucher_type == voucher_type,
        )
    ).all()
    highest = 0
    for number in numbers:
        suffix = number.rsplit("-", 1)[-1]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{prefix}-{highest + 1:04d}"


def make_line_from_input(
    db: Session,
    company_id: uuid.UUID,
    data: VoucherLineInput,
    line_number: int,
) -> VoucherLine:
    """Resolve a reviewer-supplied line against the company Ledger Master."""
    ledger = None
    if data.ledger_id is not None:
        ledger = get_ledger(db, company_id, data.ledger_id)
    elif data.ledger_name:
        ledger = find_ledger_by_name(db, company_id, data.ledger_name)
    return VoucherLine(
        company_id=company_id,
        line_number=line_number,
        ledger_id=ledger.id if ledger is not None else data.ledger_id,
        ledger_name=ledger.name if ledger is not None else (data.ledger_name or ""),
        side=data.side,
        amount=data.amount,
        narration=data.narration,
        line_type=data.line_type or "LEDGER",
        item_name=data.item_name,
        hsn_sac=data.hsn_sac,
        billed_quantity=data.billed_quantity,
        item_rate=data.item_rate,
        item_rate_per=data.item_rate_per,
        item_amount=data.item_amount,
        tax_type=data.tax_type,
    )


def build_lines(
    db: Session,
    company_id: uuid.UUID,
    inputs: Iterable[VoucherLineInput],
) -> list[VoucherLine]:
    return [
        make_line_from_input(db, company_id, item, index)
        for index, item in enumerate(inputs, start=1)
    ]


def _normalise_extra(
    extra: Sequence[VoucherIssue | VoucherIssueResponse | dict[str, Any]] | None,
) -> list[VoucherIssue]:
    result: list[VoucherIssue] = []
    for issue in extra or []:
        if isinstance(issue, VoucherIssue):
            result.append(issue)
        elif isinstance(issue, VoucherIssueResponse):
            result.append(
                VoucherIssue(
                    code=issue.code,
                    message=issue.message,
                    field=issue.field,
                    severity=issue.severity,
                )
            )
        else:
            result.append(
                VoucherIssue(
                    code=str(issue.get("code", "INVALID_VOUCHER")),
                    message=str(issue.get("message", "Voucher validation failed.")),
                    field=issue.get("field"),
                    severity=str(issue.get("severity", "ERROR")),
                )
            )
    return result


def persist_validation(
    db: Session,
    voucher: Voucher,
    extra: Sequence[VoucherIssue | VoucherIssueResponse | dict[str, Any]] | None = None,
) -> list[VoucherIssue]:
    """Recompute deterministic issues and store the reviewer-facing snapshot."""
    issues = _normalise_extra(extra) + validate_voucher(db, voucher)
    deduped: list[VoucherIssue] = []
    seen: set[tuple[str, str | None, str]] = set()
    for issue in issues:
        key = (issue.code, issue.field, issue.message)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    voucher.validation_issues = issues_as_dicts(deduped)
    return deduped


def _record_status(
    db: Session,
    voucher: Voucher,
    old: VoucherStatus,
    new: VoucherStatus,
    *,
    actor: str,
    event_type: str,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    record_event(
        db,
        company_id=voucher.company_id,
        entity_type=AccountingEntityType.VOUCHER,
        entity_id=voucher.id,
        voucher_id=voucher.id,
        event_type=event_type,
        actor=actor,
        previous_status=old.value,
        new_status=new.value,
        reason=reason,
        metadata=metadata,
    )


def create_manual_voucher(
    db: Session, company_id: uuid.UUID, data: VoucherCreate, *, actor: str = "local-user"
) -> Voucher:
    get_company(db, company_id)
    voucher = Voucher(
        company_id=company_id,
        voucher_date=data.voucher_date,
        voucher_type=data.voucher_type,
        voucher_number=next_voucher_number(db, company_id, data.voucher_type),
        status=VoucherStatus.DRAFT,
        narration=data.narration,
        source_type=VoucherSourceType.MANUAL,
        source_id=None,
        currency=data.currency,
        created_by=actor or "local-user",
    )
    voucher.lines = build_lines(db, company_id, data.lines)
    db.add(voucher)
    db.flush()
    issues = persist_validation(db, voucher)
    if issues:
        voucher.status = VoucherStatus.NEEDS_REVIEW
    record_event(
        db,
        company_id=company_id,
        entity_type=AccountingEntityType.VOUCHER,
        entity_id=voucher.id,
        voucher_id=voucher.id,
        event_type="VOUCHER_CREATED",
        actor=actor,
        previous_status=None,
        new_status=voucher.status.value,
        metadata={"source_type": "MANUAL", "issues": issues_as_dicts(issues)},
    )
    db.commit()
    db.refresh(voucher)
    return voucher


def _snapshot_lines(voucher: Voucher) -> list[dict[str, Any]]:
    return [
        {
            "line_number": line.line_number,
            "ledger_id": str(line.ledger_id) if line.ledger_id else None,
            "ledger_name": line.ledger_name,
            "side": line.side.value,
            "amount": str(line.amount),
            "line_type": line.line_type,
        }
        for line in voucher.lines
    ]


def update_voucher(
    db: Session, voucher: Voucher, data: VoucherUpdate, *, actor: str = "local-user"
) -> Voucher:
    if voucher.status not in (
        VoucherStatus.DRAFT,
        VoucherStatus.NEEDS_REVIEW,
        VoucherStatus.REJECTED,
    ):
        raise VoucherValidationError(
            "INVALID_STATUS_TRANSITION",
            f"A voucher in {voucher.status.value} cannot be edited.",
        )

    payload = data.model_dump(exclude_unset=True)
    line_inputs = data.lines if "lines" in data.model_fields_set else None
    payload.pop("lines", None)
    old_status = voucher.status

    for field, value in payload.items():
        if value is None and field in ("voucher_date", "voucher_type", "currency"):
            continue
        old = getattr(voucher, field)
        if old != value:
            record_event(
                db,
                company_id=voucher.company_id,
                entity_type=AccountingEntityType.VOUCHER,
                entity_id=voucher.id,
                voucher_id=voucher.id,
                event_type="VOUCHER_UPDATED",
                actor=actor,
                field=field,
                old_value=old,
                new_value=value,
                previous_status=old_status.value,
                new_status=old_status.value,
            )
            setattr(voucher, field, value)

    if line_inputs is not None:
        old_lines = _snapshot_lines(voucher)
        # Delete the old rows before inserting the replacements: the
        # (voucher_id, line_number) unique constraint must not see both sets at
        # the same time.
        voucher.lines.clear()
        db.flush()
        voucher.lines = build_lines(db, voucher.company_id, line_inputs)
        new_lines = _snapshot_lines(voucher)
        record_event(
            db,
            company_id=voucher.company_id,
            entity_type=AccountingEntityType.VOUCHER,
            entity_id=voucher.id,
            voucher_id=voucher.id,
            event_type="VOUCHER_LINES_REPLACED",
            actor=actor,
            old_value=str(old_lines)[:4000],
            new_value=str(new_lines)[:4000],
            previous_status=old_status.value,
            new_status=old_status.value,
            metadata={"old_lines": old_lines, "new_lines": new_lines},
        )
        old_by_number = {line["line_number"]: line for line in old_lines}
        for line in new_lines:
            previous = old_by_number.get(line["line_number"])
            if previous is not None and previous.get("ledger_id") != line.get("ledger_id"):
                record_event(
                    db,
                    company_id=voucher.company_id,
                    entity_type=AccountingEntityType.VOUCHER,
                    entity_id=voucher.id,
                    voucher_id=voucher.id,
                    event_type="LEDGER_CHANGED",
                    actor=actor,
                    field=f"line.{line['line_number']}.ledger",
                    old_value=previous.get("ledger_name"),
                    new_value=line.get("ledger_name"),
                    previous_status=old_status.value,
                    new_status=old_status.value,
                )

    db.flush()
    issues = persist_validation(db, voucher)
    if issues:
        voucher.status = VoucherStatus.NEEDS_REVIEW
    elif voucher.status in (VoucherStatus.NEEDS_REVIEW, VoucherStatus.REJECTED):
        voucher.status = VoucherStatus.DRAFT
    if voucher.status is not old_status:
        _record_status(
            db,
            voucher,
            old_status,
            voucher.status,
            actor=actor,
            event_type="VOUCHER_STATUS_CHANGED",
            reason="Validation result after editing.",
            metadata={"issues": issues_as_dicts(issues)},
        )
    db.commit()
    db.refresh(voucher)
    return voucher


def delete_voucher(
    db: Session, voucher: Voucher, *, actor: str = "local-user"
) -> None:
    if voucher.status not in (
        VoucherStatus.DRAFT,
        VoucherStatus.NEEDS_REVIEW,
        VoucherStatus.REJECTED,
    ):
        raise VoucherValidationError(
            "INVALID_STATUS_TRANSITION",
            f"A voucher in {voucher.status.value} cannot be deleted.",
        )
    record_event(
        db,
        company_id=voucher.company_id,
        entity_type=AccountingEntityType.VOUCHER,
        entity_id=voucher.id,
        voucher_id=voucher.id,
        event_type="VOUCHER_DELETED",
        actor=actor,
        previous_status=voucher.status.value,
        new_status=None,
        metadata={
            "voucher_number": voucher.voucher_number,
            "voucher_type": voucher.voucher_type.value,
            "source_type": voucher.source_type.value,
            "source_id": str(voucher.source_id) if voucher.source_id else None,
            "lines": _snapshot_lines(voucher),
        },
    )
    db.delete(voucher)
    db.commit()


def submit_voucher(
    db: Session, voucher: Voucher, *, actor: str = "local-user"
) -> Voucher:
    if voucher.status not in (VoucherStatus.DRAFT, VoucherStatus.NEEDS_REVIEW):
        raise VoucherTransitionError(
            f"A voucher in {voucher.status.value} cannot be submitted for approval."
        )
    old = voucher.status
    db.flush()
    issues = persist_validation(db, voucher)
    if issues:
        voucher.status = VoucherStatus.NEEDS_REVIEW
        _record_status(
            db,
            voucher,
            old,
            voucher.status,
            actor=actor,
            event_type="VOUCHER_SUBMITTED_WITH_EXCEPTIONS",
            reason="Validation issues route the voucher to review.",
            metadata={"issues": issues_as_dicts(issues)},
        )
        db.commit()
        db.refresh(voucher)
        return voucher

    voucher.status = VoucherStatus.PENDING_APPROVAL
    _record_status(
        db,
        voucher,
        old,
        voucher.status,
        actor=actor,
        event_type="VOUCHER_SUBMITTED_FOR_APPROVAL",
    )
    db.commit()
    db.refresh(voucher)
    return voucher


def approve_voucher(
    db: Session, voucher: Voucher, *, actor: str = "local-user", comment: str | None = None
) -> Voucher:
    if voucher.status is not VoucherStatus.PENDING_APPROVAL:
        raise VoucherTransitionError(
            f"A voucher in {voucher.status.value} cannot be approved."
        )
    old = voucher.status
    db.flush()
    issues = persist_validation(db, voucher)
    if issues:
        voucher.status = VoucherStatus.NEEDS_REVIEW
        _record_status(
            db,
            voucher,
            old,
            voucher.status,
            actor=actor,
            event_type="VOUCHER_APPROVAL_BLOCKED",
            reason="Validation issues prevent approval.",
            metadata={"issues": issues_as_dicts(issues)},
        )
        db.commit()
        first = issues[0]
        raise VoucherValidationError(first.code, first.message, issues)

    voucher.status = VoucherStatus.APPROVED
    voucher.approved_by = actor or "local-user"
    voucher.approved_at = datetime.now(timezone.utc)
    _record_status(
        db,
        voucher,
        old,
        voucher.status,
        actor=actor,
        event_type="VOUCHER_APPROVED",
        reason=comment,
    )
    _record_approved_mappings(db, voucher, actor=actor)
    db.commit()
    db.refresh(voucher)
    return voucher


def reject_voucher(
    db: Session,
    voucher: Voucher,
    *,
    actor: str = "local-user",
    reason: str | None = None,
) -> Voucher:
    if voucher.status not in (VoucherStatus.PENDING_APPROVAL, VoucherStatus.NEEDS_REVIEW):
        raise VoucherTransitionError(
            f"A voucher in {voucher.status.value} cannot be rejected."
        )
    cleaned_reason = (reason or "").strip()
    if not cleaned_reason:
        raise VoucherValidationError(
            "REJECTION_REASON_REQUIRED",
            "A rejection reason is required so the correction is traceable.",
        )
    old = voucher.status
    voucher.status = VoucherStatus.REJECTED
    voucher.rejected_by = actor or "local-user"
    voucher.rejected_at = datetime.now(timezone.utc)
    voucher.rejection_reason = cleaned_reason
    _record_status(
        db,
        voucher,
        old,
        voucher.status,
        actor=actor,
        event_type="VOUCHER_REJECTED",
        reason=voucher.rejection_reason,
    )
    db.commit()
    db.refresh(voucher)
    return voucher


def reopen_voucher(
    db: Session, voucher: Voucher, *, actor: str = "local-user"
) -> Voucher:
    if voucher.status not in (
        VoucherStatus.REJECTED,
        VoucherStatus.POSTING_FAILED,
        VoucherStatus.NEEDS_REVIEW,
    ):
        raise VoucherTransitionError(
            f"A voucher in {voucher.status.value} cannot be reopened."
        )
    old = voucher.status
    voucher.status = VoucherStatus.DRAFT
    _record_status(
        db,
        voucher,
        old,
        voucher.status,
        actor=actor,
        event_type="VOUCHER_REOPENED",
        reason="Returned to draft for correction.",
    )
    db.commit()
    db.refresh(voucher)
    return voucher


def mark_vouchers_exported(
    db: Session,
    vouchers: Sequence[Voucher],
    *,
    actor: str = "local-user",
    filename: str | None = None,
) -> None:
    """Mark approved vouchers as awaiting import confirmation.

    This is deliberately *not* a posting. A generated workbook only proves that
    the file was produced; `POSTED` requires a verified external reference.
    """
    if not vouchers:
        return
    now = datetime.now(timezone.utc)
    for voucher in vouchers:
        if voucher.status not in (
            VoucherStatus.APPROVED,
            VoucherStatus.AWAITING_IMPORT_CONFIRMATION,
        ):
            raise VoucherValidationError(
                "APPROVAL_REQUIRED",
                (
                    f"Voucher {voucher.voucher_number} is in {voucher.status.value}; "
                    "only approved vouchers can be exported as an import file."
                ),
            )
    for voucher in vouchers:
        old = voucher.status
        if voucher.status is VoucherStatus.APPROVED:
            voucher.status = VoucherStatus.AWAITING_IMPORT_CONFIRMATION
        voucher.exported_at = now
        voucher.exported_by = actor or "local-user"
        voucher.export_count = (voucher.export_count or 0) + 1
        _record_status(
            db,
            voucher,
            old,
            voucher.status,
            actor=actor,
            event_type="VOUCHER_EXPORTED",
            metadata={
                "filename": filename,
                "export_count": voucher.export_count,
                "posted": False,
            },
        )
    db.commit()


def verify_posting(
    db: Session,
    voucher: Voucher,
    *,
    actor: str = "local-user",
    verified: bool,
    external_reference: str | None = None,
    note: str | None = None,
) -> Voucher:
    if voucher.status not in (
        VoucherStatus.AWAITING_IMPORT_CONFIRMATION,
        VoucherStatus.POSTING_IN_PROGRESS,
        VoucherStatus.POSTING_FAILED,
    ):
        raise VoucherTransitionError(
            f"A voucher in {voucher.status.value} cannot be verified for posting."
        )
    old = voucher.status
    if verified:
        if not external_reference or not external_reference.strip():
            raise VoucherValidationError(
                "POSTING_NOT_VERIFIED",
                "A verified posting requires an external posting reference.",
            )
        voucher.status = VoucherStatus.POSTED
        voucher.posting_reference = external_reference.strip()
        voucher.posted_at = datetime.now(timezone.utc)
        voucher.posting_error = None
        event_type = "VOUCHER_POSTED_VERIFIED"
        reason = note
    else:
        voucher.status = VoucherStatus.POSTING_FAILED
        voucher.posting_error = note or "Posting was not verified."
        event_type = "VOUCHER_POSTING_FAILED"
        reason = voucher.posting_error
    _record_status(
        db,
        voucher,
        old,
        voucher.status,
        actor=actor,
        event_type=event_type,
        reason=reason,
        metadata={"external_reference": external_reference, "verified": verified},
    )
    db.commit()
    db.refresh(voucher)
    return voucher


def _record_approved_mappings(
    db: Session, voucher: Voucher, *, actor: str
) -> None:
    """Persist explainable mapping evidence from an approved voucher.

    This is not an approval itself. It only remembers which ledger the human
    approved, so the next suggestion can cite a real prior decision.
    """
    if voucher.source_id is None:
        return
    if voucher.source_type is VoucherSourceType.INVOICE:
        invoice = db.scalar(
            select(Invoice).where(
                Invoice.id == voucher.source_id,
                Invoice.company_id == voucher.company_id,
            )
        )
        if invoice is None or invoice.vendor_id is None:
            return
        candidates = [
            line
            for line in voucher.lines
            if line.ledger_id is not None and line.line_type != "TAX"
        ]
        if voucher.voucher_type is VoucherType.PURCHASE:
            candidates = [line for line in candidates if line.side is VoucherSide.DR]
        elif voucher.voucher_type is VoucherType.SALES:
            candidates = [line for line in candidates if line.side is VoucherSide.CR]
        ledger_ids = {line.ledger_id for line in candidates}
        if len(ledger_ids) != 1:
            return
        ledger_id = next(iter(ledger_ids))
        existing = db.scalar(
            select(AccountingMapping).where(
                AccountingMapping.company_id == voucher.company_id,
                AccountingMapping.source_type == AccountingMappingSourceType.INVOICE,
                AccountingMapping.match_type == AccountingMappingMatchType.VENDOR,
                AccountingMapping.vendor_id == invoice.vendor_id,
                AccountingMapping.voucher_type == voucher.voucher_type.value,
                AccountingMapping.ledger_id == ledger_id,
                AccountingMapping.is_active.is_(True),
            )
        )
        if existing is not None:
            return
        mapping = AccountingMapping(
            company_id=voucher.company_id,
            source_type=AccountingMappingSourceType.INVOICE,
            match_type=AccountingMappingMatchType.VENDOR,
            vendor_id=invoice.vendor_id,
            voucher_type=voucher.voucher_type.value,
            ledger_id=ledger_id,
            reason=f"Approved voucher {voucher.voucher_number} used this ledger.",
            approved_by=actor,
            approved_at=datetime.now(timezone.utc),
        )
        db.add(mapping)
        db.flush()
        record_event(
            db,
            company_id=voucher.company_id,
            entity_type=AccountingEntityType.MAPPING,
            entity_id=mapping.id,
            voucher_id=voucher.id,
            event_type="MAPPING_CREATED_FROM_APPROVED_VOUCHER",
            actor=actor,
            new_status="ACTIVE",
            reason=mapping.reason,
            metadata={"voucher_id": str(voucher.id), "ledger_id": str(ledger_id)},
        )
        return

    if voucher.source_type is VoucherSourceType.BANK_TRANSACTION:
        transaction = db.scalar(
            select(BankTransaction).where(
                BankTransaction.id == voucher.source_id,
                BankTransaction.company_id == voucher.company_id,
            )
        )
        if transaction is None or not transaction.counterparty_name:
            return
        statement = db.scalar(
            select(BankStatement).where(
                BankStatement.id == transaction.bank_statement_id,
                BankStatement.company_id == voucher.company_id,
            )
        )
        account = (
            db.scalar(
                select(BankAccount).where(
                    BankAccount.id == statement.bank_account_id,
                    BankAccount.company_id == voucher.company_id,
                )
            )
            if statement is not None
            else None
        )
        bank_name = (account.ledger_name or "").strip().lower() if account else ""
        candidates = [
            line
            for line in voucher.lines
            if line.ledger_id is not None
            and line.ledger_name.strip().lower() != bank_name
        ]
        ledger_ids = {line.ledger_id for line in candidates}
        if len(ledger_ids) != 1:
            return
        ledger_id = next(iter(ledger_ids))
        existing = db.scalar(
            select(AccountingMapping).where(
                AccountingMapping.company_id == voucher.company_id,
                AccountingMapping.source_type
                == AccountingMappingSourceType.BANK_TRANSACTION,
                AccountingMapping.match_type
                == AccountingMappingMatchType.COUNTERPARTY,
                AccountingMapping.counterparty_name.ilike(
                    transaction.counterparty_name.strip()
                ),
                AccountingMapping.voucher_type == voucher.voucher_type.value,
                AccountingMapping.ledger_id == ledger_id,
                AccountingMapping.is_active.is_(True),
            )
        )
        if existing is not None:
            return
        mapping = AccountingMapping(
            company_id=voucher.company_id,
            source_type=AccountingMappingSourceType.BANK_TRANSACTION,
            match_type=AccountingMappingMatchType.COUNTERPARTY,
            counterparty_name=transaction.counterparty_name,
            voucher_type=voucher.voucher_type.value,
            ledger_id=ledger_id,
            reason=f"Approved voucher {voucher.voucher_number} used this ledger.",
            approved_by=actor,
            approved_at=datetime.now(timezone.utc),
        )
        db.add(mapping)
        db.flush()
        record_event(
            db,
            company_id=voucher.company_id,
            entity_type=AccountingEntityType.MAPPING,
            entity_id=mapping.id,
            voucher_id=voucher.id,
            event_type="MAPPING_CREATED_FROM_APPROVED_VOUCHER",
            actor=actor,
            new_status="ACTIVE",
            reason=mapping.reason,
            metadata={"voucher_id": str(voucher.id), "ledger_id": str(ledger_id)},
        )


__all__ = [
    "VoucherNotFoundError",
    "approve_voucher",
    "build_lines",
    "create_manual_voucher",
    "delete_voucher",
    "get_voucher",
    "list_vouchers",
    "make_line_from_input",
    "mark_vouchers_exported",
    "next_voucher_number",
    "persist_validation",
    "reject_voucher",
    "reopen_voucher",
    "submit_voucher",
    "update_voucher",
    "verify_posting",
]
