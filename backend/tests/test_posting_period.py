"""Unit tests for the Tally posting-date rule (no database needed)."""

from __future__ import annotations

from datetime import date

from app.services.posting_period import resolve_tally_posting_date

MONTH = date(2026, 10, 15)  # any day of October 2026


def test_invoice_before_period_snaps_to_first_of_month() -> None:
    assert resolve_tally_posting_date(date(2026, 9, 18), MONTH) == date(2026, 10, 1)


def test_invoice_in_same_month_keeps_its_date() -> None:
    assert resolve_tally_posting_date(date(2026, 10, 16), MONTH) == date(2026, 10, 16)


def test_invoice_after_period_keeps_its_date() -> None:
    assert resolve_tally_posting_date(date(2026, 11, 5), MONTH) == date(2026, 11, 5)


def test_no_posting_month_keeps_voucher_date() -> None:
    assert resolve_tally_posting_date(date(2026, 9, 18), None) == date(2026, 9, 18)
