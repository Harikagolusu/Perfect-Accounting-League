"""OCR abstraction for scanned PDFs.

OCR is a fallback, never the first choice: digital PDFs are read from their
text layer. When that layer is unusable, this module can turn page images into
text with Tesseract (via `pytesseract`). The invoice extractor depends only on
the `OCRProcessor` protocol, so a different engine can be swapped in without
touching invoice logic.

The engine is optional at runtime. If Tesseract is not installed, `is_available`
returns False and `extract_text` raises `OCRUnavailableError` with a safe
message; the extractor then falls back to the existing vision provider. No
binary path, provider credential or document content is ever logged or returned.
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Protocol, runtime_checkable

import pymupdf

from app.services.document_errors import DocumentPreparationError
from app.services.text_quality import PageText

logger = logging.getLogger(__name__)

#: OCR rendering resolution. Higher than the vision fallback because OCR needs
#: crisp glyph edges; still bounded by the existing page limit.
OCR_RENDER_DPI = 220


class OCRUnavailableError(Exception):
    """No OCR engine is available in this deployment."""


@runtime_checkable
class OCRProcessor(Protocol):
    """Turns a scanned PDF into text, one page at a time."""

    name: str

    def is_available(self) -> bool:
        """Whether the engine can actually run right now."""
        ...

    def extract_text(self, raw_bytes: bytes, *, max_pages: int) -> tuple[PageText, ...]:
        """Read text from the document. Raises OCRUnavailableError if absent."""
        ...


class TesseractOCRProcessor:
    """Tesseract-backed OCR for PDF page images."""

    name = "tesseract"

    def __init__(self) -> None:
        self._available: bool | None = None

    def is_available(self) -> bool:
        if self._available is None:
            self._available = self._probe()
        return self._available

    def extract_text(self, raw_bytes: bytes, *, max_pages: int) -> tuple[PageText, ...]:
        if max_pages < 1:
            raise DocumentPreparationError(
                DocumentPreparationError.INVALID_CONFIGURATION,
                "PDF page processing is disabled by configuration.",
            )
        if not self.is_available():
            raise OCRUnavailableError("No OCR engine is available.")

        try:
            import pytesseract
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise OCRUnavailableError("No OCR engine is available.") from exc

        try:
            with pymupdf.open(stream=raw_bytes, filetype="pdf") as pdf:
                if pdf.needs_pass:
                    raise DocumentPreparationError(
                        DocumentPreparationError.DOCUMENT_ENCRYPTED,
                        "The PDF is password-protected and cannot be read.",
                    )
                if pdf.page_count < 1:
                    raise DocumentPreparationError(
                        DocumentPreparationError.INVALID_DOCUMENT,
                        "The PDF contains no pages.",
                    )
                if pdf.page_count > max_pages:
                    raise DocumentPreparationError(
                        DocumentPreparationError.DOCUMENT_TOO_MANY_PAGES,
                        f"The PDF has {pdf.page_count} pages; the configured limit "
                        f"is {max_pages}. Split the document or raise "
                        "INVOICE_MAX_PAGES.",
                    )

                pages: list[PageText] = []
                for index, page in enumerate(pdf):
                    pixmap = page.get_pixmap(dpi=OCR_RENDER_DPI, alpha=False)
                    with Image.open(BytesIO(pixmap.tobytes("png"))) as image:
                        text = pytesseract.image_to_string(image, lang="eng")
                    pages.append(
                        PageText(page_number=index + 1, text=text.strip())
                    )
                return tuple(pages)
        except (DocumentPreparationError, OCRUnavailableError):
            raise
        except Exception as exc:
            # The class name is enough to diagnose a missing language pack or a
            # corrupt page; the message may contain a local path.
            logger.warning("OCR failed (%s)", type(exc).__name__)
            raise OCRUnavailableError("The OCR engine could not process the document.") from exc

    def _probe(self) -> bool:
        try:
            import pytesseract

            pytesseract.get_tesseract_version()
            return True
        except Exception as exc:
            logger.info("OCR engine is not available (%s)", type(exc).__name__)
            return False


def get_ocr_processor() -> OCRProcessor:
    """Return the OCR engine to use. A single swap point for deployments."""
    return TesseractOCRProcessor()


__all__ = [
    "OCR_RENDER_DPI",
    "OCRProcessor",
    "OCRUnavailableError",
    "TesseractOCRProcessor",
    "get_ocr_processor",
]
