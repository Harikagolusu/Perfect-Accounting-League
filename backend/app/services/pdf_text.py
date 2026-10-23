"""Native PDF text extraction with page boundaries.

Digital PDFs already contain a text layer. Rasterising them to images throws
that text away, costs vision tokens and loses exact printed values. This module
reads the text layer with PyMuPDF instead, one page at a time, so:

* the existing page limit is enforced before any page is processed;
* each page keeps its own boundary, which lets the prompt see page structure;
* no intermediate image is produced and the stored bytes are never modified.

An empty or unusable text layer is not an error here: the caller checks the
result with `text_quality` and decides whether OCR or vision is needed.
"""

from __future__ import annotations

import logging

import pymupdf

from app.services.document_errors import DocumentPreparationError
from app.services.text_quality import PageText

logger = logging.getLogger(__name__)


class PdfText:
    """The text layer of a PDF, split by page."""

    def __init__(self, pages: tuple[PageText, ...], page_count: int) -> None:
        self.pages = pages
        self.page_count = page_count

    @property
    def text(self) -> str:
        return combine_pages(self.pages)


def inspect_pdf(raw_bytes: bytes, *, max_pages: int) -> int:
    """Validate a PDF and return its page count.

    Raises `DocumentPreparationError` with a stable code. Used by the router so
    an oversized or encrypted PDF fails before text extraction or rendering.
    """
    if max_pages < 1:
        raise DocumentPreparationError(
            DocumentPreparationError.INVALID_CONFIGURATION,
            "PDF page processing is disabled by configuration.",
        )
    try:
        with pymupdf.open(stream=raw_bytes, filetype="pdf") as pdf:
            _check_pdf(pdf, max_pages=max_pages)
            return pdf.page_count
    except DocumentPreparationError:
        raise
    except Exception as exc:
        logger.warning("Could not inspect PDF (%s)", type(exc).__name__)
        raise DocumentPreparationError(
            DocumentPreparationError.DOCUMENT_UNREADABLE,
            "The PDF could not be read.",
        ) from exc


def read_pdf_text(raw_bytes: bytes, *, max_pages: int) -> PdfText:
    """Read the native text layer of a PDF, page by page.

    The returned text is normalised only for whitespace: lines are stripped at
    the end and runs of blank lines are collapsed. Printed values are otherwise
    untouched.
    """
    if max_pages < 1:
        raise DocumentPreparationError(
            DocumentPreparationError.INVALID_CONFIGURATION,
            "PDF page processing is disabled by configuration.",
        )
    try:
        with pymupdf.open(stream=raw_bytes, filetype="pdf") as pdf:
            _check_pdf(pdf, max_pages=max_pages)
            pages = tuple(
                PageText(page_number=index + 1, text=_normalise_page_text(page.get_text("text")))
                for index, page in enumerate(pdf)
            )
            return PdfText(pages=pages, page_count=pdf.page_count)
    except DocumentPreparationError:
        raise
    except Exception as exc:
        logger.warning("Could not read PDF text (%s)", type(exc).__name__)
        raise DocumentPreparationError(
            DocumentPreparationError.DOCUMENT_UNREADABLE,
            "The PDF could not be read.",
        ) from exc


def combine_pages(pages: tuple[PageText, ...] | list[PageText]) -> str:
    """Join pages with explicit boundaries the model can reason about."""
    if len(pages) == 1:
        return pages[0].text
    parts: list[str] = []
    for page in pages:
        parts.append(f"--- Page {page.page_number} of {len(pages)} ---\n{page.text}")
    return "\n\n".join(parts)


def _check_pdf(pdf: pymupdf.Document, *, max_pages: int) -> None:
    if pdf.needs_pass:
        raise DocumentPreparationError(
            DocumentPreparationError.DOCUMENT_ENCRYPTED,
            "The PDF is password-protected and cannot be read.",
        )
    page_count = pdf.page_count
    if page_count < 1:
        raise DocumentPreparationError(
            DocumentPreparationError.INVALID_DOCUMENT,
            "The PDF contains no pages.",
        )
    if page_count > max_pages:
        # Never truncate: the rest of the document might hold the total or a
        # tax summary.
        raise DocumentPreparationError(
            DocumentPreparationError.DOCUMENT_TOO_MANY_PAGES,
            f"The PDF has {page_count} pages; the configured limit is "
            f"{max_pages}. Split the document or raise INVOICE_MAX_PAGES.",
        )


def _normalise_page_text(text: str) -> str:
    """Trim line ends and collapse blank-line runs without touching values."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    collapsed: list[str] = []
    blank_run = 0
    for line in lines:
        if line.strip():
            blank_run = 0
            collapsed.append(line)
        else:
            blank_run += 1
            # One blank line is meaningful separation; more is noise.
            if blank_run == 1:
                collapsed.append("")
    return "\n".join(collapsed).strip()


__all__ = ["PdfText", "combine_pages", "inspect_pdf", "read_pdf_text"]
