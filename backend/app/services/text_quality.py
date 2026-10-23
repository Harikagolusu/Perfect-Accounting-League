"""Deterministic text-quality checks for extracted document text.

The extraction pipeline must never treat `len(text) > 0` as proof that a
document has usable text. A scanned PDF can produce a few stray characters, a
broken PDF can produce a page of control codes, and an OCR pass can return
noise. This module turns those cases into a small, explainable decision so the
caller can choose the next processing path (native text, OCR or vision).

The checks are intentionally simple and conservative:

* enough non-whitespace characters to hold an invoice header;
* a healthy share of letters/digits rather than symbols or control codes;
* text on at least half of the pages;
* at least one invoice-like keyword.

They decide *path selection only*. They never change, repair or interpret an
extracted value, and they never decide whether a document is an invoice — that
remains the model's job under the existing validation rules.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class PageText:
    """Text read from one page of a document.

    `page_number` is 1-based and matches the order a person sees. The text is
    kept exactly as extracted apart from surrounding whitespace.
    """

    page_number: int
    text: str


@dataclass(frozen=True)
class TextQuality:
    """Result of assessing extracted text.

    `reason` is a stable machine-readable code. `usable` is the only field the
    pipeline branches on; the counters exist so the decision can be explained
    in tests and logs without exposing document content.
    """

    usable: bool
    reason: str
    meaningful_characters: int
    alphanumeric_characters: int
    alphanumeric_ratio: float
    pages_with_text: int
    total_pages: int
    invoice_signals: int


#: Why a text payload was accepted or rejected.
TEXT_EMPTY = "TEXT_EMPTY"
TEXT_TOO_SHORT = "TEXT_TOO_SHORT"
TEXT_LOW_ALPHANUMERIC_RATIO = "TEXT_LOW_ALPHANUMERIC_RATIO"
TEXT_INSUFFICIENT_PAGE_COVERAGE = "TEXT_INSUFFICIENT_PAGE_COVERAGE"
TEXT_NO_INVOICE_SIGNALS = "TEXT_NO_INVOICE_SIGNALS"
TEXT_USABLE = "TEXT_USABLE"

#: Below this many non-whitespace characters there is not enough text to hold
#: an invoice number, date and total together.
MIN_MEANINGFUL_CHARACTERS = 100
#: A page needs this much text to count as "has text" during coverage checks.
MIN_PAGE_CHARACTERS = 20
#: Text must be mostly letters/digits. Scanned noise and control sequences sit
#: far below this ratio; normal invoices sit far above it.
MIN_ALPHANUMERIC_RATIO = 0.55
#: Distinct invoice-like keywords required. One is enough: an invoice may be a
#: single line ("TAX INVOICE") plus a table.
MIN_INVOICE_SIGNALS = 1

_INVOICE_KEYWORDS: tuple[str, ...] = (
    "invoice",
    "tax invoice",
    "bill",
    "gstin",
    "gst",
    "pan",
    "total",
    "amount",
    "subtotal",
    "taxable",
    "cgst",
    "sgst",
    "igst",
    "cess",
    "due date",
    "invoice date",
    "purchase order",
    "po number",
    "buyer",
    "supplier",
    "vendor",
    "customer",
    "ship to",
    "bill to",
)


def _is_meaningful(character: str) -> bool:
    """Whitespace and control characters do not count as document content."""
    if character.isspace():
        return False
    category = unicodedata.category(character)
    return not category.startswith("C")


def assess_text_quality(
    pages: list[PageText] | tuple[PageText, ...],
    *,
    min_meaningful_characters: int = MIN_MEANINGFUL_CHARACTERS,
) -> TextQuality:
    """Assess whether `pages` holds enough real text for an LLM text pass.

    `min_meaningful_characters` lets a structured document (a small spreadsheet
    row or a short DOCX header) use a lower bar than a scanned PDF. All other
    checks are unchanged.

    The function is pure: it has no I/O and never raises for empty input.
    """
    total_pages = max(len(pages), 1)
    combined = "\n".join(page.text for page in pages)
    meaningful = sum(1 for character in combined if _is_meaningful(character))
    alphanumeric = sum(1 for character in combined if character.isalnum())
    ratio = alphanumeric / meaningful if meaningful else 0.0
    pages_with_text = sum(
        1
        for page in pages
        if sum(1 for character in page.text if _is_meaningful(character))
        >= MIN_PAGE_CHARACTERS
    )
    lowered = combined.casefold()
    invoice_signals = sum(1 for keyword in _INVOICE_KEYWORDS if keyword in lowered)

    if meaningful == 0:
        return _result(
            False,
            TEXT_EMPTY,
            meaningful,
            alphanumeric,
            ratio,
            pages_with_text,
            total_pages,
            invoice_signals,
        )
    if meaningful < min_meaningful_characters:
        return _result(
            False,
            TEXT_TOO_SHORT,
            meaningful,
            alphanumeric,
            ratio,
            pages_with_text,
            total_pages,
            invoice_signals,
        )
    if ratio < MIN_ALPHANUMERIC_RATIO:
        return _result(
            False,
            TEXT_LOW_ALPHANUMERIC_RATIO,
            meaningful,
            alphanumeric,
            ratio,
            pages_with_text,
            total_pages,
            invoice_signals,
        )
    if pages_with_text < max(1, math.ceil(total_pages / 2)):
        return _result(
            False,
            TEXT_INSUFFICIENT_PAGE_COVERAGE,
            meaningful,
            alphanumeric,
            ratio,
            pages_with_text,
            total_pages,
            invoice_signals,
        )
    if invoice_signals < MIN_INVOICE_SIGNALS:
        return _result(
            False,
            TEXT_NO_INVOICE_SIGNALS,
            meaningful,
            alphanumeric,
            ratio,
            pages_with_text,
            total_pages,
            invoice_signals,
        )
    return _result(
        True,
        TEXT_USABLE,
        meaningful,
        alphanumeric,
        ratio,
        pages_with_text,
        total_pages,
        invoice_signals,
    )


def _result(
    usable: bool,
    reason: str,
    meaningful: int,
    alphanumeric: int,
    ratio: float,
    pages_with_text: int,
    total_pages: int,
    invoice_signals: int,
) -> TextQuality:
    return TextQuality(
        usable=usable,
        reason=reason,
        meaningful_characters=meaningful,
        alphanumeric_characters=alphanumeric,
        alphanumeric_ratio=round(ratio, 4),
        pages_with_text=pages_with_text,
        total_pages=total_pages,
        invoice_signals=invoice_signals,
    )


__all__ = [
    "MIN_ALPHANUMERIC_RATIO",
    "MIN_INVOICE_SIGNALS",
    "MIN_MEANINGFUL_CHARACTERS",
    "PageText",
    "TEXT_EMPTY",
    "TEXT_INSUFFICIENT_PAGE_COVERAGE",
    "TEXT_LOW_ALPHANUMERIC_RATIO",
    "TEXT_NO_INVOICE_SIGNALS",
    "TEXT_TOO_SHORT",
    "TEXT_USABLE",
    "TextQuality",
    "assess_text_quality",
]
