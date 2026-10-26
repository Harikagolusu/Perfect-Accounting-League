"""Deterministic voucher validation and status transitions.

Nothing here repairs a voucher. An unbalanced voucher stays unbalanced and is
routed to `NEEDS_REVIEW`; no balancing line, rounding adjustment or amount edit
is created automatically.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import Ledger
from app.models.voucher import Voucher, VoucherStatus, VoucherType

ZERO = Decimal("0")


@dataclass(frozen=True)
class VoucherIssue:
    code: str
    message: str
    field: str | None = None
    severity: str = "ERROR"

    def as_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "message": self.message,
            "field": self.field,
            "severity": self.severity,
        }


class VoucherValidationError(Exception):
    """A deterministic voucher validation failure."""

    def __init__(self, code: str, message: str, issues: list[VoucherIssue] | None = None):
        super().__init__(message)
        self.code = code
        self.issues = issues or [VoucherIssue(code, message)]


class VoucherTransitionError(Exception):
    """An invalid voucher status transition."""


#: The client's workflow. DRAFT can never jump straight to POSTED.
ALLOWED_TRANSITIONS: dict[VoucherStatus, set[VoucherStatus]] = {
    VoucherStatus.DRAFT: {
        VoucherStatus.PENDING_APPROVAL,
        VoucherStatus.NEEDS_REVIEW,
        VoucherStatus.REJECTED,
    },
    VoucherStatus.NEEDS_REVIEW: {
        VoucherStatus.DRAFT,
        VoucherStatus.PENDING_APPROVAL,
        VoucherStatus.REJECTED,
    },
    VoucherStatus.PENDING_APPROVAL: {
        VoucherStatus.APPROVED,
        VoucherStatus.REJECTED,
        VoucherStatus.NEEDS_REVIEW,
    },
    VoucherStatus.REJECTED: {
        VoucherStatus.DRAFT,
        VoucherStatus.NEEDS_REVIEW,
    },
    VoucherStatus.APPROVED: {
        VoucherStatus.POSTING_IN_PROGRESS,
        VoucherStatus.AWAITING_IMPORT_CONFIRMATION,
        VoucherStatus.NEEDS_REVIEW,
    },
    VoucherStatus.AWAITING_IMPORT_CONFIRMATION: {
        VoucherStatus.POSTED,
        VoucherStatus.POSTING_FAILED,
        VoucherStatus.NEEDS_REVIEW,
    },
    VoucherStatus.POSTING_IN_PROGRESS: {
        VoucherStatus.POSTED,
        VoucherStatus.POSTING_FAILED,
        VoucherStatus.NEEDS_REVIEW,
    },
    VoucherStatus.POSTING_FAILED: {
        VoucherStatus.POSTING_IN_PROGRESS,
        VoucherStatus.NEEDS_REVIEW,
        VoucherStatus.DRAFT,
    },
    VoucherStatus.POSTED: set(),
}


def assert_transition_allowed(current: VoucherStatus, target: VoucherStatus) -> None:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise VoucherTransitionError(
            f"Voucher status cannot move from {current.value} to {target.value}."
        )


def _ledger_exists(
    db: Session, company_id: uuid.UUID, ledger_id: uuid.UUID
) -> tuple[Ledger | None, bool]:
    ledger = db.scalar(
        select(Ledger).where(
            Ledger.id == ledger_id, Ledger.company_id == company_id
        )
    )
    if ledger is None:
        return None, False
    return ledger, ledger.is_active


def validate_voucher(db: Session, voucher: Voucher) -> list[VoucherIssue]:
    """Return every structural reason this voucher is unsafe to approve/export."""
    issues: list[VoucherIssue] = []

    if voucher.voucher_date is None:
        issues.append(
            VoucherIssue(
                "VOUCHER_DATE_REQUIRED",
                "A voucher date is required before approval.",
                "Voucher Date",
            )
        )

    if voucher.voucher_type is None:
        issues.append(
            VoucherIssue(
                "VOUCHER_TYPE_REQUIRED",
                "A voucher type is required before approval.",
                "Voucher Type",
            )
        )

    if not voucher.lines:
        issues.append(
            VoucherIssue(
                "VOUCHER_HAS_NO_LEDGER_ROWS",
                "A voucher must contain at least one ledger line.",
                "Voucher Lines",
            )
        )
        return issues

    seen_numbers: set[int] = set()
    for line in voucher.lines:
        if line.line_number in seen_numbers:
            issues.append(
                VoucherIssue(
                    "DUPLICATE_LINE_NUMBER",
                    f"Line number {line.line_number} appears more than once.",
                    "Line Number",
                )
            )
        seen_numbers.add(line.line_number)

        if line.amount is None or line.amount <= ZERO:
            issues.append(
                VoucherIssue(
                    "LEDGER_AMOUNT_REQUIRED",
                    f"Ledger line {line.line_number} must have a positive amount.",
                    "Ledger Amount",
                )
            )

        if line.ledger_id is None:
            issues.append(
                VoucherIssue(
                    "MISSING_LEDGER",
                    (
                        f"Ledger line {line.line_number} is not linked to a "
                        "company ledger master record."
                    ),
                    "Ledger Name",
                )
            )
        else:
            ledger, active = _ledger_exists(db, voucher.company_id, line.ledger_id)
            if ledger is None:
                issues.append(
                    VoucherIssue(
                        "MISSING_LEDGER",
                        (
                            f"Ledger line {line.line_number} references a ledger "
                            "that does not belong to this company."
                        ),
                        "Ledger Name",
                    )
                )
            elif not active:
                issues.append(
                    VoucherIssue(
                        "LEDGER_INACTIVE",
                        f"Ledger '{ledger.name}' is inactive.",
                        "Ledger Name",
                    )
                )
            elif not line.ledger_name.strip():
                issues.append(
                    VoucherIssue(
                        "MISSING_LEDGER",
                        f"Ledger line {line.line_number} has no ledger name.",
                        "Ledger Name",
                    )
                )

    if not any(issue.code == "LEDGER_AMOUNT_REQUIRED" for issue in issues):
        debit = sum(
            (line.amount for line in voucher.lines if line.side.value == "Dr"),
            ZERO,
        )
        credit = sum(
            (line.amount for line in voucher.lines if line.side.value == "Cr"),
            ZERO,
        )
        if debit != credit:
            issues.append(
                VoucherIssue(
                    "VOUCHER_NOT_BALANCED",
                    (
                        f"Voucher is not balanced: debit {debit} does not equal "
                        f"credit {credit}."
                    ),
                    "Ledger Amount",
                )
            )

    return issues


def approval_issues(db: Session, voucher: Voucher) -> list[VoucherIssue]:
    """Validation plus the mandatory approval-state check."""
    issues = validate_voucher(db, voucher)
    if voucher.status is not VoucherStatus.PENDING_APPROVAL:
        issues.append(
            VoucherIssue(
                "APPROVAL_REQUIRED",
                "The voucher must be submitted for approval before it can be approved.",
                "Status",
            )
        )
    return issues


def has_errors(issues: list[VoucherIssue]) -> bool:
    return any(issue.severity == "ERROR" for issue in issues)


def issues_as_dicts(issues: list[VoucherIssue]) -> list[dict[str, str | None]]:
    return [issue.as_dict() for issue in issues]


__all__ = [
    "ALLOWED_TRANSITIONS",
    "VoucherIssue",
    "VoucherTransitionError",
    "VoucherValidationError",
    "approval_issues",
    "assert_transition_allowed",
    "has_errors",
    "issues_as_dicts",
    "validate_voucher",
]
