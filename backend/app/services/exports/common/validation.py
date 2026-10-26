"""Validation helpers shared by the client-format export mappers.

Nothing here repairs a voucher or a row. A mismatch is returned as an issue and
the caller excludes the record from the workbook.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from app.services.exports.common.models import (
    ExportIssue,
    Voucher,
    VoucherLine,
)

#: Same tolerance as invoice validation: absorbs representation noise only.
MONEY_TOLERANCE = Decimal("0.01")
ZERO = Decimal("0")


def error(
    code: str,
    message: str,
    *,
    record_type: str,
    record_id: str | None = None,
    field: str | None = None,
) -> ExportIssue:
    return ExportIssue(
        code=code,
        message=message,
        severity="ERROR",
        record_type=record_type,
        record_id=record_id,
        field=field,
    )


def warning(
    code: str,
    message: str,
    *,
    record_type: str,
    record_id: str | None = None,
    field: str | None = None,
) -> ExportIssue:
    return ExportIssue(
        code=code,
        message=message,
        severity="WARNING",
        record_type=record_type,
        record_id=record_id,
        field=field,
    )


def ledger_amount(value: Decimal | None) -> Decimal:
    """Coin a missing amount as zero only for summation, never for output."""
    return value if value is not None else ZERO


def voucher_balance_issues(voucher: Voucher) -> list[ExportIssue]:
    """Return structural problems that make a voucher unsafe to export."""
    issues: list[ExportIssue] = []
    record_id = voucher.source_id
    record_type = voucher.source_type

    if not voucher.lines:
        issues.append(
            error(
                "VOUCHER_HAS_NO_LEDGER_ROWS",
                "A voucher must contain at least one ledger row.",
                record_type=record_type,
                record_id=record_id,
            )
        )
        return issues

    for line in voucher.lines:
        if not line.ledger_name.strip():
            issues.append(
                error(
                    "LEDGER_MAPPING_REQUIRED",
                    "A ledger row has no ledger name.",
                    record_type=record_type,
                    record_id=record_id,
                    field="Ledger Name",
                )
            )
        if line.amount <= ZERO:
            issues.append(
                error(
                    "LEDGER_AMOUNT_REQUIRED",
                    f"Ledger '{line.ledger_name}' must have a positive amount.",
                    record_type=record_type,
                    record_id=record_id,
                    field="Ledger Amount",
                )
            )

    debit = sum(
        (ledger_amount(line.amount) for line in voucher.lines if line.side == "Dr"),
        ZERO,
    )
    credit = sum(
        (ledger_amount(line.amount) for line in voucher.lines if line.side == "Cr"),
        ZERO,
    )
    if abs(debit - credit) > MONEY_TOLERANCE:
        issues.append(
            error(
                "VOUCHER_NOT_BALANCED",
                (
                    f"Voucher is not balanced: debit {debit} does not equal "
                    f"credit {credit}. It was not exported."
                ),
                record_type=record_type,
                record_id=record_id,
            )
        )
    return issues


def assign_voucher_numbers(vouchers: list[Voucher]) -> tuple[Voucher, ...]:
    """Sort deterministically and number each voucher type from 1.

    The client's bank sample restarts voucher numbers per voucher type, so the
    same rule is used for both voucher workbooks. Numbering is derived from the
    canonical records only; it is never persisted.
    """
    ordered = sorted(
        vouchers,
        key=lambda v: (
            v.voucher_date,
            v.voucher_type,
            v.source_type,
            v.source_id,
        ),
    )
    counters: dict[str, int] = {}
    numbered: list[Voucher] = []
    for voucher in ordered:
        counters[voucher.voucher_type] = counters.get(voucher.voucher_type, 0) + 1
        numbered.append(
            replace(voucher, voucher_number=counters[voucher.voucher_type])
        )
    return tuple(numbered)


def row_debits_credits(lines: tuple[VoucherLine, ...]) -> tuple[Decimal, Decimal]:
    """Expose a voucher's totals for tests and diagnostics."""
    debit = sum((line.amount for line in lines if line.side == "Dr"), ZERO)
    credit = sum((line.amount for line in lines if line.side == "Cr"), ZERO)
    return debit, credit


__all__ = [
    "MONEY_TOLERANCE",
    "assign_voucher_numbers",
    "error",
    "ledger_amount",
    "row_debits_credits",
    "voucher_balance_issues",
    "warning",
]
