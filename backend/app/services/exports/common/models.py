"""Shared value objects for deterministic client-format exports.

The export layer never reads or writes Excel column names in the domain layer.
These objects describe rows, vouchers and issues; each template-specific mapper
turns them into the exact columns the client supplied.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal

IssueSeverity = Literal["ERROR", "WARNING"]
Side = Literal["Dr", "Cr"]


@dataclass(frozen=True)
class ExportIssue:
    """One reason a record or field was not exported."""

    code: str
    message: str
    severity: IssueSeverity
    record_type: str
    record_id: str | None = None
    field: str | None = None


@dataclass(frozen=True)
class VoucherLine:
    """One ledger row inside a voucher."""

    ledger_name: str
    amount: Decimal
    side: Side
    #: Party details belong on the party ledger row. They stay optional.
    party_address: str | None = None
    party_pincode: str | None = None
    party_gstin: str | None = None
    #: Tally supports item details on a ledger row. The client's bank sample
    #: leaves them blank, but the contract supports them.
    item_name: str | None = None
    billed_quantity: Decimal | None = None
    item_rate: Decimal | None = None
    item_rate_per: str | None = None
    item_amount: Decimal | None = None
    ledger_narration: str | None = None


@dataclass(frozen=True)
class Voucher:
    """One accounting voucher with one or more ledger rows."""

    voucher_date: date
    voucher_type: str
    change_mode: str
    source_type: Literal["INVOICE", "BANK_TRANSACTION", "MANUAL"]
    source_id: str
    lines: tuple[VoucherLine, ...]
    narration: str | None = None
    original_invoice_no: str | None = None
    voucher_number: int = 0


@dataclass(frozen=True)
class ExportBuild:
    """Rows and issues produced by one export mapper."""

    rows: tuple[Any, ...]
    issues: tuple[ExportIssue, ...]
    voucher_count: int = 0

    @property
    def errors(self) -> tuple[ExportIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "ERROR")

    @property
    def warnings(self) -> tuple[ExportIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "WARNING")

    @property
    def excluded_records(self) -> int:
        return len(
            {
                issue.record_id
                for issue in self.errors
                if issue.record_id is not None
            }
        )


@dataclass(frozen=True)
class VoucherBuild:
    """Vouchers and issues produced by a mapper before a workbook is written."""

    vouchers: tuple[Voucher, ...]
    issues: tuple[ExportIssue, ...]

    @property
    def errors(self) -> tuple[ExportIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "ERROR")

    @property
    def warnings(self) -> tuple[ExportIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "WARNING")

    @property
    def excluded_records(self) -> int:
        return len(
            {
                issue.record_id
                for issue in self.errors
                if issue.record_id is not None
            }
        )


__all__ = [
    "ExportBuild",
    "ExportIssue",
    "IssueSeverity",
    "Side",
    "Voucher",
    "VoucherBuild",
    "VoucherLine",
]
