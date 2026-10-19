"""Invoice validation and status-transition rules.

Kept out of the router and out of the service's database work so the rules are
readable, testable on their own, and reusable by any future caller (a CLI, a
background job, a different API version).

Scope discipline
----------------
These are structural checks only. No tax law lives here: no GST rates, no TDS
rates, no IGST/place-of-supply reasoning, no invoice-numbering rules. Those
need CA confirmation and belong to the GST/TDS steps.

Accounting safety
-----------------
Nothing here ever repairs a value. An inconsistent invoice is reported as an
issue for a human to resolve; the stored numbers are left exactly as entered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.models.invoice import InvoiceStatus

#: Half-paise tolerance when comparing money. Any real discrepancy is far
#: larger, so this only absorbs representation noise, never hides an error.
MONEY_TOLERANCE = Decimal("0.01")


#: The only permitted moves. Anything absent here is rejected.
#:
#: DRAFT ──> PROCESSING ──> PROCESSED / NEEDS_REVIEW / FAILED
#: FAILED ──> PROCESSING (retry), never straight to PROCESSED: a retry has to
#:            actually run again.
#: NEEDS_REVIEW ──> PROCESSING / PROCESSED (a human resolved it)
#: PROCESSED is terminal; nothing follows without an explicit reopen feature.
ALLOWED_TRANSITIONS: dict[InvoiceStatus, frozenset[InvoiceStatus]] = {
    InvoiceStatus.DRAFT: frozenset({InvoiceStatus.PROCESSING, InvoiceStatus.NEEDS_REVIEW}),
    InvoiceStatus.PROCESSING: frozenset(
        {InvoiceStatus.NEEDS_REVIEW, InvoiceStatus.PROCESSED, InvoiceStatus.FAILED}
    ),
    InvoiceStatus.FAILED: frozenset({InvoiceStatus.PROCESSING}),
    InvoiceStatus.NEEDS_REVIEW: frozenset(
        {InvoiceStatus.PROCESSING, InvoiceStatus.PROCESSED}
    ),
    InvoiceStatus.PROCESSED: frozenset(),
}

#: Which transitions mean "a human is resolving this", and so may be driven
#: from the edit endpoint as well as the processing endpoint.
MANUAL_TRANSITIONS = frozenset({InvoiceStatus.NEEDS_REVIEW, InvoiceStatus.PROCESSED})


@dataclass(frozen=True)
class InvoiceValidationResult:
    """Outcome of validating one invoice's field values."""

    issues: tuple[str, ...] = field(default=())

    @property
    def is_valid(self) -> bool:
        return not self.issues

    def as_list(self) -> list[str]:
        return list(self.issues)


class InvoiceTransitionError(Exception):
    """A status change was requested that the lifecycle does not allow."""


def is_transition_allowed(current: InvoiceStatus, target: InvoiceStatus) -> bool:
    """True when `current -> target` is part of the documented lifecycle."""
    return target in ALLOWED_TRANSITIONS[current]


def assert_transition_allowed(current: InvoiceStatus, target: InvoiceStatus) -> None:
    """Raise unless the transition is permitted."""
    if current is target:
        return
    if not is_transition_allowed(current, target):
        allowed = ", ".join(sorted(s.value for s in ALLOWED_TRANSITIONS[current])) or "none"
        raise InvoiceTransitionError(
            f"Cannot move an invoice from {current.value} to {target.value}. "
            f"Allowed from {current.value}: {allowed}."
        )


def _optional_decimal(value: Decimal | None) -> Decimal:
    return Decimal("0") if value is None else value


def validate_invoice_fields(
    *,
    invoice_number: str | None = None,
    invoice_date: date | None = None,
    due_date: date | None = None,
    vendor_gstin: str | None = None,
    vendor_pan: str | None = None,
    buyer_gstin: str | None = None,
    taxable_amount: Decimal | None = None,
    cgst_amount: Decimal | None = None,
    sgst_amount: Decimal | None = None,
    igst_amount: Decimal | None = None,
    cess_amount: Decimal | None = None,
    round_off: Decimal | None = None,
    total_amount: Decimal | None = None,
) -> InvoiceValidationResult:
    """Check one invoice's values and report every problem found.

    Returns all issues at once so a reviewer can fix them in one pass. Values
    are never altered.
    """
    issues: list[str] = []

    if invoice_date and due_date and due_date < invoice_date:
        issues.append("Due date is before the invoice date.")
    if invoice_date and invoice_date > date.today():
        issues.append("Invoice date is in the future.")

    if invoice_number is not None and len(invoice_number) > 128:
        issues.append("Invoice number is longer than 128 characters.")

    # Amounts other than round-off must not be negative. Round-off legitimately
    # can be, when a total is rounded down.
    for label, amount in (
        ("Taxable amount", taxable_amount),
        ("CGST", cgst_amount),
        ("SGST", sgst_amount),
        ("IGST", igst_amount),
        ("Cess", cess_amount),
        ("Total amount", total_amount),
    ):
        if amount is not None and amount < 0:
            issues.append(f"{label} cannot be negative.")

    if vendor_gstin and len(vendor_gstin) != 15:
        issues.append("Vendor GSTIN must be 15 characters.")
    if buyer_gstin and len(buyer_gstin) != 15:
        issues.append("Buyer GSTIN must be 15 characters.")
    if vendor_pan and len(vendor_pan) != 10:
        issues.append("Vendor PAN must be 10 characters.")

    # Totals are only checked when enough of them were actually supplied.
    if taxable_amount is not None and total_amount is not None:
        tax_total = _optional_decimal(cgst_amount) + _optional_decimal(sgst_amount) + _optional_decimal(
            igst_amount
        ) + _optional_decimal(cess_amount)
        computed = taxable_amount + tax_total + _optional_decimal(round_off)
        if abs(computed - total_amount) > MONEY_TOLERANCE:
            # Reported, never corrected: the entered total is the source.
            issues.append(
                "Total amount does not match taxable + tax + round-off "
                f"({computed} vs {total_amount}). Review the figures."
            )

    if invoice_date and invoice_number is None:
        issues.append("An invoice date without an invoice number is incomplete.")

    return InvoiceValidationResult(tuple(issues))