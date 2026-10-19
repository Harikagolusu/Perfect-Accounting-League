"""Bank domain validation.

Structural rules only: period sanity, debit/credit consistency and a balance
reconciliation check. No accounting meaning is inferred anywhere here — a bank
debit is not treated as an expense and a credit is not treated as income,
because that depends on context this step does not have.

Two classes of problem
----------------------
A **fatal** issue means the row should not exist: an inverted statement period,
a transaction that is both a debit and a credit, a negative amount. Those are
rejected.

A **review** issue means the row is unusual but real: a negative balance on an
overdraft, a value date preceding the transaction date. Those are stored and
surfaced, because guessing wrong about them would corrupt genuine data.

Accounting safety
-----------------
Nothing is ever corrected. A mismatch is reported as an issue for a human to
resolve, and the stored figures are left exactly as entered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.bank import TransactionType

#: Half-paise tolerance when comparing money.
MONEY_TOLERANCE = Decimal("0.01")

ZERO = Decimal("0")


class BankValidationError(Exception):
    """A value that cannot be stored at all.

    Distinct from `BankTransitionError`, which is about status changes.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class BankTransitionError(Exception):
    """A statement status change the lifecycle does not allow."""


@dataclass(frozen=True)
class BankValidationResult:
    """Outcome of validating one set of bank values."""

    issues: tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.issues

    @property
    def fatal(self) -> frozenset[str]:
        return self.issues_set & FATAL_ISSUES

    @property
    def issues_set(self) -> frozenset[str]:
        return frozenset(self.issues)

    def as_list(self) -> list[str]:
        return list(self.issues)


#: Mirrors the invoice lifecycle. A statement follows the same shape: a retry
#: must actually retry, and nothing is PROCESSED without a parser.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "UPLOADED": frozenset({"PROCESSING", "NEEDS_REVIEW"}),
    "PROCESSING": frozenset({"PROCESSED", "NEEDS_REVIEW", "FAILED"}),
    "FAILED": frozenset({"PROCESSING"}),
    "NEEDS_REVIEW": frozenset({"PROCESSED", "PROCESSING"}),
    "PROCESSED": frozenset(),
}

# Issue text is the stable contract between this module and its callers, so the
# fatal sets live here rather than being re-typed at each call site.

#: An inverted period is not a real period. It is never silently swapped.
PERIOD_INVERTED = "Statement end date is before the start date."
BOTH_DIRECTIONS = "A transaction cannot be both a debit and a credit."
NO_DIRECTION = "A transaction needs a debit or a credit amount."
NEGATIVE_AMOUNT = "Debit and credit amounts cannot be negative."

FATAL_ISSUES: frozenset[str] = frozenset(
    {PERIOD_INVERTED, BOTH_DIRECTIONS, NO_DIRECTION, NEGATIVE_AMOUNT}
)


def assert_transition_allowed(current: str, target: str) -> None:
    """Raise unless the status change is part of the documented lifecycle."""
    if current == target:
        return
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        allowed_text = ", ".join(sorted(allowed)) or "none"
        raise BankTransitionError(
            f"Cannot move a bank statement from {current} to {target}. "
            f"Allowed from {current}: {allowed_text}."
        )


def reject_if_fatal(result: BankValidationResult) -> None:
    """Raise if any issue makes the row unstorable."""
    blocking = result.fatal
    if blocking:
        raise BankValidationError("; ".join(sorted(blocking)))


def derive_transaction_type(
    debit_amount: Decimal, credit_amount: Decimal
) -> TransactionType:
    """Work out the direction from the amounts.

    Deriving rather than trusting a client-supplied value is what keeps the
    column from ever disagreeing with the amounts it summarises.
    """
    return TransactionType.DEBIT if debit_amount > ZERO else TransactionType.CREDIT


def validate_statement(
    *,
    statement_start_date: date | None = None,
    statement_end_date: date | None = None,
    opening_balance: Decimal | None = None,
    closing_balance: Decimal | None = None,
) -> BankValidationResult:
    """Validate a statement's period and balances."""
    issues: list[str] = []

    if statement_start_date and statement_end_date and statement_end_date < statement_start_date:
        issues.append(PERIOD_INVERTED)

    for label, balance in (
        ("Opening balance", opening_balance),
        ("Closing balance", closing_balance),
    ):
        if balance is not None and balance < ZERO:
            # An overdraft legitimately shows negative, so this is a review
            # flag rather than a rejection: only a human can say it is wrong.
            issues.append(f"{label} is negative.")

    return BankValidationResult(tuple(issues))


def validate_transaction(
    *,
    transaction_date: date,
    value_date: date | None = None,
    debit_amount: Decimal | None = None,
    credit_amount: Decimal | None = None,
    balance: Decimal | None = None,
) -> BankValidationResult:
    """Validate one transaction's dates and debit/credit pairing.

    A valid transaction moves money exactly one way: a positive debit with no
    credit, or a positive credit with no debit. Zero on both sides is not a
    transaction at all.
    """
    issues: list[str] = []

    if value_date is not None and value_date < transaction_date:
        # Some banks value-date earlier than they post, so this is a review
        # flag, not a rejection.
        issues.append("Value date is before the transaction date.")

    debit = ZERO if debit_amount is None else debit_amount
    credit = ZERO if credit_amount is None else credit_amount

    if debit < ZERO or credit < ZERO:
        issues.append(NEGATIVE_AMOUNT)
    elif debit > ZERO and credit > ZERO:
        issues.append(BOTH_DIRECTIONS)
    elif debit == ZERO and credit == ZERO:
        issues.append(NO_DIRECTION)

    if balance is not None and balance < ZERO:
        issues.append("Balance is negative; confirm this is an overdraft.")

    return BankValidationResult(tuple(issues))


def reconcile_balances(
    *,
    opening_balance: Decimal | None,
    closing_balance: Decimal | None,
    debits: list[Decimal],
    credits: list[Decimal],
) -> BankValidationResult:
    """Check that opening + credits - debits reaches the closing balance.

    Only runs when every figure needed is present. The transaction order is
    never assumed and no amount is altered: a mismatch is simply reported, so
    the data a human entered survives for them to check.
    """
    if opening_balance is None or closing_balance is None:
        return BankValidationResult()

    expected = opening_balance + sum(credits, ZERO) - sum(debits, ZERO)
    if abs(expected - closing_balance) > MONEY_TOLERANCE:
        return BankValidationResult(
            (
                "Closing balance does not match opening + credits - debits "
                f"({expected} vs {closing_balance}). Review the statement.",
            )
        )
    return BankValidationResult()