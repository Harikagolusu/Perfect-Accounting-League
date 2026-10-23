"""Deterministic text-quality checks.

These tests do not need a database or a provider: the quality decision is the
whole unit under test.
"""

from __future__ import annotations

from app.services.text_quality import (
    TEXT_EMPTY,
    TEXT_INSUFFICIENT_PAGE_COVERAGE,
    TEXT_LOW_ALPHANUMERIC_RATIO,
    TEXT_NO_INVOICE_SIGNALS,
    TEXT_TOO_SHORT,
    TEXT_USABLE,
    PageText,
    assess_text_quality,
)

RICH_TEXT = (
    "TAX INVOICE\n"
    "Deccan Freight Carriers\n"
    "GSTIN: 27AACCD1234F1Z2\n"
    "Invoice No: DFC/2026/0042\n"
    "Invoice Date: 15/03/2026\n"
    "Taxable Amount: 1,000.00\n"
    "Grand Total: 1,180.00\n"
)


def test_empty_text_is_not_usable() -> None:
    result = assess_text_quality((PageText(1, ""),))

    assert result.usable is False
    assert result.reason == TEXT_EMPTY
    assert result.meaningful_characters == 0


def test_a_few_stray_characters_are_not_usable() -> None:
    result = assess_text_quality((PageText(1, "T A X"),))

    assert result.usable is False
    assert result.reason == TEXT_TOO_SHORT


def test_symbol_noise_is_not_usable() -> None:
    # Enough printable characters, but almost none of them are alphanumeric.
    result = assess_text_quality((PageText(1, "!" * 300),))

    assert result.usable is False
    assert result.reason == TEXT_LOW_ALPHANUMERIC_RATIO


def test_one_text_page_in_a_multi_page_document_is_not_usable() -> None:
    pages = (
        PageText(1, RICH_TEXT),
        PageText(2, ""),
        PageText(3, ""),
        PageText(4, ""),
    )

    result = assess_text_quality(pages)

    assert result.usable is False
    assert result.reason == TEXT_INSUFFICIENT_PAGE_COVERAGE
    assert result.pages_with_text == 1
    assert result.total_pages == 4


def test_text_without_invoice_signals_is_not_usable() -> None:
    text = (
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
        "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
        "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris."
    )

    result = assess_text_quality((PageText(1, text),))

    assert result.usable is False
    assert result.reason == TEXT_NO_INVOICE_SIGNALS


def test_real_invoice_text_is_usable() -> None:
    result = assess_text_quality((PageText(1, RICH_TEXT),))

    assert result.usable is True
    assert result.reason == TEXT_USABLE
    assert result.alphanumeric_ratio >= 0.55
    assert result.invoice_signals >= 1


def test_counters_are_explainable_without_document_content() -> None:
    result = assess_text_quality(
        (PageText(1, RICH_TEXT), PageText(2, RICH_TEXT))
    )

    assert result.total_pages == 2
    assert result.pages_with_text == 2
    assert result.meaningful_characters > 0
    assert result.alphanumeric_characters <= result.meaningful_characters
