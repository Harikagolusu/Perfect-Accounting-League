"""Native PDF text extraction.

Digital PDFs must be read from their text layer, with page boundaries kept.
These tests build small PDFs in memory; no stored file or provider is involved.
"""

from __future__ import annotations

import pymupdf
import pytest

from app.services.document_errors import DocumentPreparationError
from app.services.pdf_text import combine_pages, inspect_pdf, read_pdf_text


def _pdf(pages: list[list[str]] | None = None, *, blank_pages: int = 0) -> bytes:
    document = pymupdf.open()
    for lines in pages or []:
        page = document.new_page()
        for index, line in enumerate(lines):
            page.insert_text((72, 72 + index * 16), line)
    for _ in range(blank_pages):
        document.new_page()
    data = document.tobytes()
    document.close()
    return data


def test_reads_native_text_page_by_page() -> None:
    raw = _pdf(
        [
            ["TAX INVOICE", "Invoice No: A-1"],
            ["Total: 100.00", "Due Date: 14/04/2026"],
        ]
    )

    result = read_pdf_text(raw, max_pages=10)

    assert result.page_count == 2
    assert [page.page_number for page in result.pages] == [1, 2]
    assert "Invoice No: A-1" in result.pages[0].text
    assert "Total: 100.00" in result.pages[1].text
    assert "TAX INVOICE" not in result.pages[1].text


def test_combine_pages_adds_explicit_boundaries() -> None:
    raw = _pdf([["one"], ["two"], ["three"]])

    combined = combine_pages(read_pdf_text(raw, max_pages=10).pages)

    assert "--- Page 1 of 3 ---" in combined
    assert "--- Page 3 of 3 ---" in combined
    assert combined.index("one") < combined.index("two") < combined.index("three")


def test_single_page_has_no_unnecessary_marker() -> None:
    raw = _pdf([["single page"]])

    combined = combine_pages(read_pdf_text(raw, max_pages=10).pages)

    assert combined == "single page"
    assert "--- Page" not in combined


def test_a_page_without_a_text_layer_is_empty_not_an_error() -> None:
    raw = _pdf(blank_pages=1)

    result = read_pdf_text(raw, max_pages=10)

    assert len(result.pages) == 1
    assert result.pages[0].text == ""


def test_page_limit_is_enforced_before_reading() -> None:
    raw = _pdf([["one"], ["two"], ["three"]])

    with pytest.raises(DocumentPreparationError) as caught:
        read_pdf_text(raw, max_pages=2)

    assert caught.value.code == DocumentPreparationError.DOCUMENT_TOO_MANY_PAGES
    assert "3 pages" in caught.value.message


def test_inspect_pdf_returns_the_page_count() -> None:
    assert inspect_pdf(_pdf([["one"], ["two"]]), max_pages=10) == 2


def test_broken_pdf_is_unreadable() -> None:
    with pytest.raises(DocumentPreparationError) as caught:
        read_pdf_text(b"%PDF-1.7 broken", max_pages=10)

    assert caught.value.code == DocumentPreparationError.DOCUMENT_UNREADABLE


def test_zero_page_limit_is_a_configuration_error() -> None:
    with pytest.raises(DocumentPreparationError) as caught:
        read_pdf_text(_pdf([["one"]]), max_pages=0)

    assert caught.value.code == DocumentPreparationError.INVALID_CONFIGURATION
