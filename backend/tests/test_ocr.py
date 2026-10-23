"""OCR fallback abstraction.

The real Tesseract binary is optional. These tests only assert the contract:
availability is reported honestly, and an unavailable engine raises a safe
exception instead of leaking an OSError message or a local path.
"""

from __future__ import annotations

import pytest

from app.services.ocr import (
    OCRProcessor,
    OCRUnavailableError,
    TesseractOCRProcessor,
)


class _UnavailableOCR:
    name = "unavailable"

    def is_available(self) -> bool:
        return False

    def extract_text(self, raw_bytes: bytes, *, max_pages: int) -> tuple:
        raise OCRUnavailableError("No OCR engine is available.")


def test_fake_processor_satisfies_the_protocol() -> None:
    assert isinstance(_UnavailableOCR(), OCRProcessor)


def test_unavailable_engine_raises_a_safe_error() -> None:
    processor = TesseractOCRProcessor()

    # Force the probe so the test does not depend on whether Tesseract happens
    # to be installed on the machine running the suite.
    processor._available = False

    with pytest.raises(OCRUnavailableError):
        processor.extract_text(b"%PDF-1.7", max_pages=10)


def test_availability_is_a_boolean_without_exposing_paths() -> None:
    processor = TesseractOCRProcessor()

    available = processor.is_available()

    assert isinstance(available, bool)


def test_zero_page_limit_is_rejected_before_probing() -> None:
    processor = TesseractOCRProcessor()
    processor._available = True

    from app.services.document_errors import DocumentPreparationError

    with pytest.raises(DocumentPreparationError) as caught:
        processor.extract_text(b"%PDF-1.7", max_pages=0)

    assert caught.value.code == DocumentPreparationError.INVALID_CONFIGURATION
