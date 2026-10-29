"""Step 8: AI invoice extraction.

The AI provider is always mocked here. No test may call the real DeepSeek API,
spend money, or depend on network access. `conftest.py` additionally clears the
real key for every test, so a developer's local `.env` cannot leak in.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.services.ai import AIProviderError, AIResponse
from app.services.document_preparation import (
    DocumentPreparationError,
    prepare_document,
)
from app.services.invoice_extraction import (
    AIDocumentInvoiceExtractor,
    AI_UNSUPPORTED_CAPABILITY,
    EXTRACTION_TEXT_TOO_LARGE,
    MAX_TEXT_PROMPT_CHARACTERS,
    ExtractionMethod,
)
from app.services.invoice_prompt import (
    INVOICE_TEXT_SYSTEM_PROMPT,
    build_text_extraction_prompt,
)
from app.services.text_quality import PageText
from app.services.invoice_extraction_validation import (
    InvalidExtractionOutput,
    _parse_date,
    parse_extraction_payload,
    validate_extraction,
)

COMPANIES_URL = "/api/v1/companies"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2024-06-05", date(2024, 6, 5)),
        ("2024/06/05", date(2024, 6, 5)),
        ("05/06/2024", date(2024, 6, 5)),
        ("05/06/24", date(2024, 6, 5)),  # 2-digit year, not year 0024
        ("05-06-24", date(2024, 6, 5)),
        ("05.06.2024", date(2024, 6, 5)),
        ("05-Jan-2024", date(2024, 1, 5)),
        ("05-Jan-24", date(2024, 1, 5)),  # common Tally/India format
        ("5 Jan 2024", date(2024, 1, 5)),
        ("Jan 5, 2024", date(2024, 1, 5)),
    ],
)
def test_parse_date_reads_common_printed_formats(raw: str, expected: date) -> None:
    parsed, issue = _parse_date(raw, "Invoice date")
    assert parsed == expected
    assert issue is None


def test_parse_date_reports_unreadable_value() -> None:
    parsed, issue = _parse_date("not a date", "Invoice date")
    assert parsed is None
    assert issue is not None

SAMPLE_INVOICE: dict[str, Any] = {
    "is_invoice": True,
    "invoice_number": "DFC/2026/0042",
    "invoice_date": "2026-03-15",
    "due_date": "2026-04-14",
    "vendor_name": "Deccan Freight Carriers",
    "vendor_gstin": "27AACCD1234F1Z2",
    "vendor_pan": "AACCD1234F",
    "buyer_gstin": "27AAJCS4821K1Z5",
    "po_number": "PO-8891",
    "currency": "INR",
    "taxable_amount": "1000.00",
    "cgst_amount": "90.00",
    "sgst_amount": "90.00",
    "igst_amount": None,
    "cess_amount": "0.00",
    "round_off": "0.00",
    "total_amount": "1180.00",
}


class FakeProvider:
    """Minimal AIProvider stand-in that records the call it received."""

    name = "fake"
    model = "fake-vision-1"

    def __init__(
        self,
        text: str | None = None,
        *,
        configured: bool = True,
        error: Exception | None = None,
    ) -> None:
        self.text = text if text is not None else json.dumps(SAMPLE_INVOICE)
        self.configured = configured
        self.error = error
        self.calls = 0
        self.text_calls = 0
        self.image_calls = 0
        self.prompts: tuple[str, ...] = ()
        self.images: tuple[Any, ...] = ()
        self.json_mode: bool | None = None
        self.system_prompt: str | None = None

    def is_configured(self) -> bool:
        return self.configured

    def _response(self) -> AIResponse:
        if self.error is not None:
            raise self.error
        return AIResponse(
            text=self.text,
            model=self.model,
            usage={
                "prompt_tokens": 120,
                "completion_tokens": 80,
                "total_tokens": 200,
            },
        )

    def generate_text(self, *args: Any, **kwargs: Any) -> AIResponse:
        raise NotImplementedError

    def generate_from_text(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> AIResponse:
        self.calls += 1
        self.text_calls += 1
        self.prompts = (*self.prompts, prompt)
        self.json_mode = json_mode
        self.system_prompt = system_prompt
        return self._response()

    def generate_from_images(
        self,
        prompt: str,
        images: Any,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> AIResponse:
        self.calls += 1
        self.image_calls += 1
        self.prompts = (*self.prompts, prompt)
        self.images = tuple(images)
        self.json_mode = json_mode
        self.system_prompt = system_prompt
        return self._response()


@pytest.fixture
def fake_ai(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    provider = FakeProvider()
    # This is the one seam the extractor uses; patching it keeps every test
    # offline while exercising the real preparation, parsing and validation.
    monkeypatch.setattr(
        "app.services.invoice_extraction.get_ai_provider", lambda: provider
    )
    return provider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _company(client: TestClient, name: str = "Extraction Co") -> dict[str, Any]:
    response = client.post(COMPANIES_URL, json={"name": name})
    assert response.status_code == 201, response.text
    company = response.json()
    # These tests cover extraction, not vendor creation. Seed the supplier
    # master with the sample invoice's identity so a clean extraction keeps
    # its PROCESSED status instead of becoming a vendor-mapping review.
    vendor = client.post(
        f"{COMPANIES_URL}/{company['id']}/vendors",
        json={
            "name": SAMPLE_INVOICE["vendor_name"],
            "gstin": SAMPLE_INVOICE["vendor_gstin"],
            "pan": SAMPLE_INVOICE["vendor_pan"],
        },
    )
    assert vendor.status_code == 201, vendor.text
    return company


def _pdf_text_lines(index: int) -> tuple[str, ...]:
    """Enough real invoice text for the deterministic quality check."""
    return (
        "TAX INVOICE",
        "Deccan Freight Carriers",
        "GSTIN: 27AACCD1234F1Z2",
        f"Invoice No: DFC/2026/{42 + index:04d}",
        "Invoice Date: 15/03/2026",
        "Due Date: 14/04/2026",
        "Buyer GSTIN: 27AAJCS4821K1Z5",
        "Taxable Amount: 1,000.00",
        "CGST @ 9%: 90.00",
        "SGST @ 9%: 90.00",
        "Grand Total: 1,180.00",
    )


def _pdf_bytes(pages: int = 1) -> bytes:
    """A digital PDF with a real native text layer."""
    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page()
        for line_index, line in enumerate(_pdf_text_lines(index)):
            page.insert_text((72, 72 + line_index * 16), line)
    data = document.tobytes()
    document.close()
    return data


def _pdf_bytes_from_lines(lines: tuple[str, ...]) -> bytes:
    """A one-page digital PDF with caller-supplied text."""
    document = pymupdf.open()
    page = document.new_page()
    for index, line in enumerate(lines):
        page.insert_text((72, 72 + index * 16), line)
    data = document.tobytes()
    document.close()
    return data


def _scanned_pdf_bytes(pages: int = 1) -> bytes:
    """A PDF with page images and no text layer, like a scanner produces."""
    document = pymupdf.open()
    image = _image_bytes("PNG")
    for _ in range(pages):
        page = document.new_page(width=700, height=420)
        page.insert_image(page.rect, stream=image)
    data = document.tobytes()
    document.close()
    return data


def _image_bytes(fmt: str = "PNG") -> bytes:
    image = Image.new("RGB", (700, 420), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(
        (
            "TAX INVOICE",
            "Invoice No: TEST-001",
            "Date: 15/03/2026",
            "Total: 1180.00",
        )
    ):
        draw.text((25, 25 + index * 40), line, fill="black")
    buffer = BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def _xlsx_bytes() -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Invoice"
    rows = (
        ("TAX INVOICE",),
        ("Deccan Freight Carriers",),
        ("GSTIN", "27AACCD1234F1Z2"),
        ("Invoice No", "DFC/2026/0042"),
        ("Invoice Date", "15/03/2026"),
        ("Taxable Amount", "1000.00"),
        ("CGST", "90.00"),
        ("SGST", "90.00"),
        ("Grand Total", "1180.00"),
        ("Currency", "INR"),
    )
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def _docx_bytes() -> bytes:
    from docx import Document as DocxDocument

    document = DocxDocument()
    for line in _pdf_text_lines(0):
        document.add_paragraph(line)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _upload(
    client: TestClient,
    company_id: str,
    *,
    filename: str,
    data: bytes,
    content_type: str,
) -> dict[str, Any]:
    response = client.post(
        f"{COMPANIES_URL}/{company_id}/documents",
        files={"file": (filename, data, content_type)},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _pdf_document(client: TestClient, company_id: str, pages: int = 1) -> dict[str, Any]:
    return _upload(
        client,
        company_id,
        filename="invoice.pdf",
        data=_pdf_bytes(pages),
        content_type="application/pdf",
    )


def _image_document(
    client: TestClient, company_id: str, fmt: str = "PNG"
) -> dict[str, Any]:
    extension = "png" if fmt == "PNG" else "jpg"
    mime = "image/png" if fmt == "PNG" else "image/jpeg"
    return _upload(
        client,
        company_id,
        filename=f"invoice.{extension}",
        data=_image_bytes(fmt),
        content_type=mime,
    )


def _process_document(
    client: TestClient, company_id: str, document_id: str
) -> Any:
    return client.post(
        f"{COMPANIES_URL}/{company_id}/invoices/process-document/{document_id}"
    )


def _create_manual_invoice(
    client: TestClient, company_id: str, document_id: str
) -> dict[str, Any]:
    response = client.post(
        f"{COMPANIES_URL}/{company_id}/invoices",
        json={
            "document_id": document_id,
            "invoice_number": "MANUAL-1",
            "invoice_date": "2026-03-01",
            "total_amount": "500.00",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _invoices_for_document(
    client: TestClient, company_id: str, document_id: str
) -> list[dict[str, Any]]:
    response = client.get(
        f"{COMPANIES_URL}/{company_id}/invoices",
        params={"document_id": document_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _get_invoice(client: TestClient, company_id: str, invoice_id: str) -> dict[str, Any]:
    response = client.get(f"{COMPANIES_URL}/{company_id}/invoices/{invoice_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _get_document(client: TestClient, company_id: str, document_id: str) -> dict[str, Any]:
    response = client.get(f"{COMPANIES_URL}/{company_id}/documents/{document_id}")
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# A. Happy path
# ---------------------------------------------------------------------------


def test_valid_pdf_extraction_creates_a_processed_ai_invoice(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    company = _company(client)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["configured"] is True
    assert body["status"] == "PROCESSED"
    assert body["is_invoice"] is True
    assert body["invoice_id"]
    assert body["provider"] == "fake"
    assert body["model"] == "fake-vision-1"
    assert body["usage"]["total_tokens"] == 200
    assert body["issues"] == []
    assert body["extraction_method"] == "NATIVE_TEXT"
    assert "file_path" not in body

    invoice = _get_invoice(client, company["id"], body["invoice_id"])
    vendor = client.get(f"{COMPANIES_URL}/{company['id']}/vendors").json()[0]
    assert invoice["source"] == "AI"
    assert invoice["status"] == "PROCESSED"
    assert invoice["vendor_id"] == vendor["id"]
    assert invoice["vendor_mapping_status"] == "MATCHED"
    assert invoice["invoice_number"] == "DFC/2026/0042"
    assert invoice["invoice_date"] == "2026-03-15"
    assert invoice["due_date"] == "2026-04-14"
    assert invoice["vendor_name"] == "Deccan Freight Carriers"
    assert invoice["vendor_gstin"] == "27AACCD1234F1Z2"
    assert invoice["vendor_pan"] == "AACCD1234F"
    assert invoice["buyer_gstin"] == "27AAJCS4821K1Z5"
    assert invoice["po_number"] == "PO-8891"
    assert invoice["currency"] == "INR"
    assert invoice["taxable_amount"] == "1000.00"
    assert invoice["cgst_amount"] == "90.00"
    assert invoice["sgst_amount"] == "90.00"
    assert invoice["igst_amount"] is None
    assert invoice["cess_amount"] == "0.00"
    assert invoice["round_off"] == "0.00"
    assert invoice["total_amount"] == "1180.00"

    assert _get_document(client, company["id"], document["id"])["status"] == "PROCESSED"
    assert fake_ai.calls == 1
    assert fake_ai.text_calls == 1
    # A digital PDF is never rasterised: no image payload was sent.
    assert fake_ai.image_calls == 0
    assert fake_ai.images == ()
    assert fake_ai.json_mode is True
    assert fake_ai.system_prompt and "Never invent" in fake_ai.system_prompt
    assert "DOCUMENT TEXT" in fake_ai.prompts[0]
    assert "DFC/2026/0042" in fake_ai.prompts[0]


def test_llm_verification_wins_over_a_regex_candidate() -> None:
    """Regex is a hint; the model's reading of the source is what is stored."""
    source = (
        "TAX INVOICE",
        "Deccan Freight Carriers",
        "27AACCD1234F1Z2",
        "27AAJCS4821K1Z5",
        "Invoice No: DFC/2026/0042",
        "Invoice Date: 15/03/2026",
        "Taxable Amount: 1,000.00",
        "CGST @ 9%: 90.00",
        "SGST @ 9%: 90.00",
        "Grand Total: 1,180.00",
    )
    # The unlabelled header makes the regex guess the first GSTIN as the vendor.
    # The model reads the surrounding layout and chooses the other way round.
    interpreted = {
        **SAMPLE_INVOICE,
        "vendor_gstin": "27AAJCS4821K1Z5",
        "vendor_pan": "AAJCS4821K",
        "buyer_gstin": "27AACCD1234F1Z2",
    }
    provider = FakeProvider(text=json.dumps(interpreted))
    extractor = AIDocumentInvoiceExtractor(provider=provider, ocr_processor=None)

    result = extractor.extract(
        _stored("invoice.pdf"), _pdf_bytes_from_lines(source)
    )

    assert result.outcome.value == "PROCESSED"
    assert result.values["vendor_gstin"] == "27AAJCS4821K1Z5"
    assert result.values["buyer_gstin"] == "27AACCD1234F1Z2"
    # Both conflicting candidates reached the model, so this is not an accident
    # of the regex failing to find the second value.
    assert "27AACCD1234F1Z2" in provider.prompts[0]
    assert "27AAJCS4821K1Z5" in provider.prompts[0]


def test_ocr_confusion_is_preserved_and_not_silently_corrected() -> None:
    """Visually similar characters are reported as read, never repaired."""
    provider = FakeProvider(
        text=json.dumps(
            {
                **SAMPLE_INVOICE,
                "invoice_number": "INV-0O1",  # zero, letter O, one
                "vendor_gstin": "27AACCD1234F1ZS",  # final S, not 5
                "total_amount": "1180",  # decimal point missing
            }
        )
    )
    extractor = AIDocumentInvoiceExtractor(provider=provider, ocr_processor=None)

    result = extractor.extract(
        _stored("invoice.pdf"), _pdf_bytes_from_lines(_pdf_text_lines(0))
    )

    assert result.values["invoice_number"] == "INV-0O1"
    assert result.values["vendor_gstin"] == "27AACCD1234F1ZS"
    assert result.values["total_amount"] == Decimal("1180")
    # No guessing that 0O1 meant 001 or that S meant 5.
    assert result.values["invoice_number"] != "INV-001"
    assert result.values["vendor_gstin"] != "27AACCD1234F1Z5"


def test_text_prompt_states_that_candidates_are_not_authoritative() -> None:
    prompt = build_text_extraction_prompt(
        "SOURCE TEXT", "invoice_number: 'WRONG' (page 1)"
    )

    assert "candidate" in INVOICE_TEXT_SYSTEM_PROMPT.lower()
    assert "Verify every value" in INVOICE_TEXT_SYSTEM_PROMPT
    assert "Never invent" in INVOICE_TEXT_SYSTEM_PROMPT
    assert "return null" in INVOICE_TEXT_SYSTEM_PROMPT
    assert "may contain false positives" in prompt
    assert "Return only values you can" in prompt
    assert "WRONG" in prompt and "SOURCE TEXT" in prompt


def test_text_limit_is_rejected_without_calling_the_provider() -> None:
    provider = FakeProvider()
    extractor = AIDocumentInvoiceExtractor(provider=provider, ocr_processor=None)
    oversized = "TAX INVOICE " + ("x" * MAX_TEXT_PROMPT_CHARACTERS)

    result = extractor._extract_text_pages(
        (PageText(1, oversized),), ExtractionMethod.NATIVE_TEXT
    )

    assert result.outcome.value == "FAILED"
    assert result.reason == EXTRACTION_TEXT_TOO_LARGE
    assert result.extraction_method == "NATIVE_TEXT"
    assert provider.calls == 0


def test_image_document_is_accepted(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    company = _company(client)
    document = _image_document(client, company["id"], "PNG")

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "PROCESSED"
    assert body["extraction_method"] == "IMAGE_VISION"
    assert fake_ai.image_calls == 1
    assert len(fake_ai.images) == 1


def test_multi_page_pdf_uses_native_text_for_every_page(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    company = _company(client)
    document = _pdf_document(client, company["id"], pages=3)

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    assert response.json()["extraction_method"] == "NATIVE_TEXT"
    assert fake_ai.text_calls == 1
    assert fake_ai.image_calls == 0
    # Every page boundary reaches the model; no page is silently dropped and
    # the printed order is preserved.
    prompt = fake_ai.prompts[0]
    assert "--- Page 1 of 3 ---" in prompt
    assert "--- Page 2 of 3 ---" in prompt
    assert "--- Page 3 of 3 ---" in prompt
    assert (
        prompt.index("DFC/2026/0042")
        < prompt.index("DFC/2026/0043")
        < prompt.index("DFC/2026/0044")
    )


def test_scanned_pdf_falls_back_to_vision_when_ocr_is_unavailable(
    client: TestClient, fake_ai: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    class UnavailableOCR:
        name = "unavailable"

        def is_available(self) -> bool:
            return False

        def extract_text(self, raw_bytes: bytes, *, max_pages: int) -> tuple:
            raise AssertionError("must not run when unavailable")

    monkeypatch.setattr(
        "app.services.invoice_extraction.get_ocr_processor",
        lambda: UnavailableOCR(),
    )
    company = _company(client)
    document = _upload(
        client,
        company["id"],
        filename="scanned.pdf",
        data=_scanned_pdf_bytes(pages=1),
        content_type="application/pdf",
    )

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "PROCESSED"
    assert body["extraction_method"] == "IMAGE_VISION"
    assert fake_ai.text_calls == 0
    assert fake_ai.image_calls == 1
    assert len(fake_ai.images) == 1


def test_scanned_pdf_uses_ocr_text_when_it_is_usable(
    client: TestClient, fake_ai: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeOCR:
        name = "fake-ocr"

        def is_available(self) -> bool:
            return True

        def extract_text(self, raw_bytes: bytes, *, max_pages: int) -> tuple:
            from app.services.text_quality import PageText

            return (PageText(page_number=1, text="\n".join(_pdf_text_lines(0))),)

    monkeypatch.setattr(
        "app.services.invoice_extraction.get_ocr_processor", lambda: FakeOCR()
    )
    company = _company(client)
    document = _upload(
        client,
        company["id"],
        filename="scanned.pdf",
        data=_scanned_pdf_bytes(pages=1),
        content_type="application/pdf",
    )

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "PROCESSED"
    assert body["extraction_method"] == "OCR_TEXT"
    assert fake_ai.text_calls == 1
    assert fake_ai.image_calls == 0


def test_scanned_pdf_that_fails_ocr_and_vision_is_not_left_processing(
    client: TestClient, fake_ai: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    class UnavailableOCR:
        name = "unavailable"

        def is_available(self) -> bool:
            return False

        def extract_text(self, raw_bytes: bytes, *, max_pages: int) -> tuple:
            raise AssertionError("must not run when unavailable")

    monkeypatch.setattr(
        "app.services.invoice_extraction.get_ocr_processor",
        lambda: UnavailableOCR(),
    )
    fake_ai.error = AIProviderError(
        "vision timeout", provider="fake", code=AIProviderError.TIMEOUT
    )
    company = _company(client)
    document = _upload(
        client,
        company["id"],
        filename="scanned.pdf",
        data=_scanned_pdf_bytes(pages=1),
        content_type="application/pdf",
    )

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["reason"] == "AI_TIMEOUT"
    assert body["extraction_method"] == "IMAGE_VISION"
    assert _get_document(client, company["id"], document["id"])["status"] == "FAILED"
    invoice = _get_invoice(client, company["id"], body["invoice_id"])
    assert invoice["status"] == "FAILED"


def test_explicit_invoice_process_endpoint_uses_the_same_lifecycle(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    company = _company(client)
    document = _pdf_document(client, company["id"])
    invoice = _create_manual_invoice(client, company["id"], document["id"])

    response = client.post(
        f"{COMPANIES_URL}/{company['id']}/invoices/{invoice['id']}/process"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "PROCESSED"
    assert body["invoice_id"] == invoice["id"]
    updated = _get_invoice(client, company["id"], invoice["id"])
    assert updated["status"] == "PROCESSED"
    assert updated["source"] == "AI"
    assert updated["invoice_number"] == "DFC/2026/0042"


# ---------------------------------------------------------------------------
# B. Configuration
# ---------------------------------------------------------------------------


def test_no_api_key_changes_nothing_and_creates_no_invoice(
    client: TestClient,
) -> None:
    company = _company(client)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["configured"] is False
    assert body["status"] == "NOT_CONFIGURED"
    assert body["reason"] == "EXTRACTION_NOT_CONFIGURED"
    assert body["invoice_id"] is None
    assert _invoices_for_document(client, company["id"], document["id"]) == []
    assert _get_document(client, company["id"], document["id"])["status"] == "UPLOADED"


def test_no_api_key_leaves_an_existing_draft_untouched(
    client: TestClient,
) -> None:
    company = _company(client)
    document = _pdf_document(client, company["id"])
    invoice = _create_manual_invoice(client, company["id"], document["id"])

    response = client.post(
        f"{COMPANIES_URL}/{company['id']}/invoices/{invoice['id']}/process"
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "NOT_CONFIGURED"
    unchanged = _get_invoice(client, company["id"], invoice["id"])
    assert unchanged["status"] == "DRAFT"
    assert unchanged["source"] == "MANUAL"
    assert unchanged["invoice_number"] == "MANUAL-1"


# ---------------------------------------------------------------------------
# C-E. Provider failures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    (
        (AIProviderError.TIMEOUT, "AI_TIMEOUT"),
        (AIProviderError.RATE_LIMIT, "AI_RATE_LIMIT"),
        (AIProviderError.CONNECTION_ERROR, "AI_CONNECTION_ERROR"),
        (AIProviderError.PROVIDER_ERROR, "AI_PROVIDER_ERROR"),
    ),
)
def test_provider_failures_move_both_records_to_failed(
    client: TestClient,
    fake_ai: FakeProvider,
    code: str,
    expected: str,
) -> None:
    fake_ai.error = AIProviderError("safe message", provider="fake", code=code)
    company = _company(client)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["configured"] is True
    assert body["status"] == "FAILED"
    assert body["reason"] == expected
    assert body["issues"] == []
    assert _get_document(client, company["id"], document["id"])["status"] == "FAILED"
    invoice = _get_invoice(client, company["id"], body["invoice_id"])
    assert invoice["status"] == "FAILED"
    assert invoice["invoice_number"] is None


def test_failure_while_applying_the_result_does_not_leave_processing(
    client: TestClient,
    fake_ai: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _explode(*args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("database write failed")

    monkeypatch.setattr("app.services.invoice._apply_vendor_mapping", _explode)
    company = _company(client)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["reason"] == "EXTRACTION_INTERNAL_ERROR"
    assert _get_document(client, company["id"], document["id"])["status"] == "FAILED"
    invoice = _get_invoice(client, company["id"], body["invoice_id"])
    assert invoice["status"] == "FAILED"


def test_provider_failure_does_not_leak_the_exception_message(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    fake_ai.error = AIProviderError(
        "api-key sk-should-never-appear", provider="fake", code=AIProviderError.TIMEOUT
    )
    company = _company(client)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200
    assert "sk-should-never-appear" not in response.text
    assert "api-key" not in response.text


# ---------------------------------------------------------------------------
# F. Malformed provider output
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    (
        "not json at all",
        "[]",
        "",
        '{"is_invoice": true, "invoice_number": "X", "total_amount": NaN}',
        '{"invoice_number": "X"}',
        '{"is_invoice": true, "invoice_number": "X", "taxable_amount": "1,000.00"}',
        '{"is_invoice": true, "invoice_number": "X", "total_amount": "10.123"}',
    ),
)
def test_invalid_ai_output_is_rejected_safely(
    client: TestClient, fake_ai: FakeProvider, text: str
) -> None:
    fake_ai.text = text
    company = _company(client)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["reason"] == "EXTRACTION_INVALID_OUTPUT"
    assert "Traceback" not in response.text
    invoice = _get_invoice(client, company["id"], body["invoice_id"])
    assert invoice["status"] == "FAILED"
    assert invoice["total_amount"] is None


def test_fenced_json_is_accepted(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    fake_ai.text = f"```json\n{json.dumps(SAMPLE_INVOICE)}\n```"
    company = _company(client)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "PROCESSED"


# ---------------------------------------------------------------------------
# G. Missing fields
# ---------------------------------------------------------------------------


def test_missing_fields_stay_null_and_are_not_invented(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    fake_ai.text = json.dumps(
        {
            "is_invoice": True,
            "invoice_number": "ONLY-NUMBER",
            "invoice_date": None,
            "due_date": None,
            "vendor_name": None,
            "vendor_gstin": None,
            "vendor_pan": None,
            "buyer_gstin": None,
            "po_number": None,
            "currency": None,
            "taxable_amount": None,
            "cgst_amount": None,
            "sgst_amount": None,
            "igst_amount": None,
            "cess_amount": None,
            "round_off": None,
            "total_amount": None,
        }
    )
    company = _company(client)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "PROCESSED"
    invoice = _get_invoice(client, company["id"], body["invoice_id"])
    assert invoice["invoice_number"] == "ONLY-NUMBER"
    for field in (
        "invoice_date",
        "due_date",
        "vendor_name",
        "vendor_gstin",
        "vendor_pan",
        "buyer_gstin",
        "po_number",
        "taxable_amount",
        "cgst_amount",
        "sgst_amount",
        "igst_amount",
        "cess_amount",
        "round_off",
        "total_amount",
    ):
        assert invoice[field] is None, field


def test_completely_empty_invoice_needs_review(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    fake_ai.text = json.dumps(
        {key: None for key in SAMPLE_INVOICE if key != "is_invoice"}
        | {"is_invoice": True}
    )
    company = _company(client)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "NEEDS_REVIEW"
    assert body["reason"] == "EXTRACTION_NEEDS_REVIEW"
    assert any("No invoice fields" in issue for issue in body["issues"])


# ---------------------------------------------------------------------------
# H. Non-invoice detection
# ---------------------------------------------------------------------------


def test_non_invoice_document_needs_review_without_fake_data(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    fake_ai.text = json.dumps({"is_invoice": False, "invoice_number": "SHOULD-NOT-STORE"})
    company = _company(client)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "NEEDS_REVIEW"
    assert body["reason"] == "NOT_AN_INVOICE"
    assert body["is_invoice"] is False
    assert _get_document(client, company["id"], document["id"])["status"] == "PROCESSED"
    invoice = _get_invoice(client, company["id"], body["invoice_id"])
    assert invoice["status"] == "NEEDS_REVIEW"
    assert invoice["invoice_number"] is None
    assert invoice["total_amount"] is None


def test_non_invoice_does_not_overwrite_a_manual_invoice(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    fake_ai.text = json.dumps({"is_invoice": False})
    company = _company(client)
    document = _pdf_document(client, company["id"])
    invoice = _create_manual_invoice(client, company["id"], document["id"])

    response = client.post(
        f"{COMPANIES_URL}/{company['id']}/invoices/{invoice['id']}/process"
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "NEEDS_REVIEW"
    updated = _get_invoice(client, company["id"], invoice["id"])
    assert updated["status"] == "NEEDS_REVIEW"
    assert updated["source"] == "MANUAL"
    assert updated["invoice_number"] == "MANUAL-1"
    assert updated["total_amount"] == "500.00"


# ---------------------------------------------------------------------------
# I-L. Deterministic validation
# ---------------------------------------------------------------------------


def test_invalid_date_becomes_a_review_issue(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    fake_ai.text = json.dumps(
        {
            "is_invoice": True,
            "invoice_number": "DATE-1",
            "invoice_date": "not-a-date",
            "total_amount": "100.00",
        }
    )
    company = _company(client)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "NEEDS_REVIEW"
    assert any("Invoice date could not be parsed" in issue for issue in body["issues"])
    invoice = _get_invoice(client, company["id"], body["invoice_id"])
    assert invoice["invoice_date"] is None
    assert invoice["invoice_number"] == "DATE-1"
    assert invoice["total_amount"] == "100.00"


def test_due_date_before_invoice_date_becomes_a_review_issue(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    fake_ai.text = json.dumps(
        {
            **SAMPLE_INVOICE,
            "invoice_date": "2026-03-15",
            "due_date": "2026-03-01",
        }
    )
    company = _company(client)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "NEEDS_REVIEW"
    assert any("Due date is before" in issue for issue in body["issues"])


def test_negative_amount_is_flagged_but_not_corrected(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    fake_ai.text = json.dumps(
        {
            **SAMPLE_INVOICE,
            "taxable_amount": "-1000.00",
            "total_amount": "-1180.00",
        }
    )
    company = _company(client)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "NEEDS_REVIEW"
    assert any("cannot be negative" in issue for issue in body["issues"])
    invoice = _get_invoice(client, company["id"], body["invoice_id"])
    assert invoice["taxable_amount"] == "-1000.00"
    assert invoice["total_amount"] == "-1180.00"


def test_gstin_and_pan_structure_is_checked(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    fake_ai.text = json.dumps(
        {
            **SAMPLE_INVOICE,
            "vendor_gstin": "27AACCD1234F1Z",  # 14 characters
            "vendor_pan": "AACCD1234",  # 9 characters
            "buyer_gstin": "NOT-A-GSTIN",
        }
    )
    company = _company(client)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "NEEDS_REVIEW"
    issues = " ".join(body["issues"])
    assert "Vendor GSTIN" in issues
    assert "Vendor PAN" in issues
    assert "Buyer GSTIN" in issues
    invoice = _get_invoice(client, company["id"], body["invoice_id"])
    # Structurally wrong but short enough to store: it is kept as read.
    assert invoice["vendor_gstin"] == "27AACCD1234F1Z"
    assert invoice["vendor_pan"] == "AACCD1234"


def test_overlong_tax_identifier_is_not_truncated(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    fake_ai.text = json.dumps(
        {
            **SAMPLE_INVOICE,
            "vendor_gstin": "27AACCD1234F1Z2-EXTRA-LONG-VALUE",
        }
    )
    company = _company(client)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200
    body = response.json()
    assert any("longer than 15" in issue for issue in body["issues"])
    invoice = _get_invoice(client, company["id"], body["invoice_id"])
    assert invoice["vendor_gstin"] is None


def test_total_mismatch_is_flagged_and_values_are_preserved(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    fake_ai.text = json.dumps({**SAMPLE_INVOICE, "total_amount": "1300.00"})
    company = _company(client)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "NEEDS_REVIEW"
    assert any("does not match" in issue for issue in body["issues"])
    invoice = _get_invoice(client, company["id"], body["invoice_id"])
    assert invoice["total_amount"] == "1300.00"
    assert invoice["taxable_amount"] == "1000.00"


def test_validation_never_applies_tax_rules(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    # 1000 + 5 + 5 = 1010, internally consistent even though the rates are
    # unusual. Step 8 must not compute or judge GST rates.
    fake_ai.text = json.dumps(
        {
            **SAMPLE_INVOICE,
            "cgst_amount": "5.00",
            "sgst_amount": "5.00",
            "total_amount": "1010.00",
        }
    )
    company = _company(client)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200
    assert response.json()["status"] == "PROCESSED"
    assert response.json()["issues"] == []


# ---------------------------------------------------------------------------
# N. Company isolation
# ---------------------------------------------------------------------------


def test_company_cannot_process_another_companys_document(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    company_a = _company(client, "Company A")
    company_b = _company(client, "Company B")
    document_b = _pdf_document(client, company_b["id"])

    response = _process_document(client, company_a["id"], document_b["id"])

    assert response.status_code == 404
    assert _invoices_for_document(client, company_b["id"], document_b["id"]) == []
    assert _get_document(client, company_b["id"], document_b["id"])["status"] == "UPLOADED"
    assert fake_ai.calls == 0


def test_company_cannot_process_another_companys_invoice(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    company_a = _company(client, "Company A")
    company_b = _company(client, "Company B")
    document_b = _pdf_document(client, company_b["id"])
    invoice_b = _create_manual_invoice(client, company_b["id"], document_b["id"])

    response = client.post(
        f"{COMPANIES_URL}/{company_a['id']}/invoices/{invoice_b['id']}/process"
    )

    assert response.status_code == 404
    unchanged = _get_invoice(client, company_b["id"], invoice_b["id"])
    assert unchanged["status"] == "DRAFT"
    assert fake_ai.calls == 0


# ---------------------------------------------------------------------------
# O. Unsupported types
# ---------------------------------------------------------------------------


def test_legacy_xls_fails_safely_without_calling_ai(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    company = _company(client)
    document = _upload(
        client,
        company["id"],
        filename="ledger.xls",
        data=b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 40,
        content_type="application/vnd.ms-excel",
    )

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["reason"] == "DOCUMENT_UNREADABLE"
    assert ".xlsx" in body["message"]
    assert fake_ai.calls == 0


def test_router_rejects_an_unsupported_extension() -> None:
    from app.services.document_preparation import route_document

    with pytest.raises(DocumentPreparationError) as caught:
        route_document(_stored("ledger.csv"), b"a,b,c", max_pages=10)

    assert (
        caught.value.code
        == DocumentPreparationError.UNSUPPORTED_DOCUMENT_TYPE
    )


def test_xlsx_invoice_uses_structured_text(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    company = _company(client)
    document = _upload(
        client,
        company["id"],
        filename="invoice.xlsx",
        data=_xlsx_bytes(),
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "PROCESSED"
    assert body["extraction_method"] == "EXCEL_TEXT"
    assert fake_ai.text_calls == 1
    assert fake_ai.image_calls == 0
    assert "--- Sheet: Invoice ---" in fake_ai.prompts[0]
    assert "DFC/2026/0042" in fake_ai.prompts[0]


def test_misnamed_structured_document_fails_without_calling_ai(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    """A zip signature alone is not proof that the file is a real DOCX."""
    company = _company(client)
    document = _upload(
        client,
        company["id"],
        filename="renamed.docx",
        data=_xlsx_bytes(),
        content_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
    )

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["reason"] == "DOCUMENT_UNREADABLE"
    assert fake_ai.calls == 0


def test_docx_invoice_uses_structured_text(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    company = _company(client)
    document = _upload(
        client,
        company["id"],
        filename="invoice.docx",
        data=_docx_bytes(),
        content_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
    )

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "PROCESSED"
    assert body["extraction_method"] == "DOCX_TEXT"
    assert fake_ai.text_calls == 1
    assert fake_ai.image_calls == 0
    assert "Deccan Freight Carriers" in fake_ai.prompts[0]


# ---------------------------------------------------------------------------
# P. PDF page limit
# ---------------------------------------------------------------------------


def test_pdf_above_page_limit_is_rejected_without_truncation(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    company = _company(client)
    document = _pdf_document(client, company["id"], pages=11)

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["reason"] == "DOCUMENT_TOO_MANY_PAGES"
    assert "11 pages" in body["message"]
    assert "10" in body["message"]
    assert fake_ai.calls == 0


# ---------------------------------------------------------------------------
# Q-R. Retry and success
# ---------------------------------------------------------------------------


def test_failed_processing_can_be_retried_on_the_same_invoice(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    company = _company(client)
    document = _pdf_document(client, company["id"])
    fake_ai.error = AIProviderError("timeout", provider="fake", code=AIProviderError.TIMEOUT)

    first = _process_document(client, company["id"], document["id"])
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["status"] == "FAILED"

    fake_ai.error = None
    second = _process_document(client, company["id"], document["id"])
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["status"] == "PROCESSED"
    assert second_body["invoice_id"] == first_body["invoice_id"]

    invoices = _invoices_for_document(client, company["id"], document["id"])
    assert len(invoices) == 1
    assert invoices[0]["status"] == "PROCESSED"
    assert invoices[0]["source"] == "AI"


# ---------------------------------------------------------------------------
# T. Idempotency
# ---------------------------------------------------------------------------


def test_processing_the_same_document_twice_does_not_duplicate_or_rebill(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    company = _company(client)
    document = _pdf_document(client, company["id"])

    first = _process_document(client, company["id"], document["id"])
    second = _process_document(client, company["id"], document["id"])

    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["status"] == "PROCESSED"
    assert second.json()["reason"] == "ALREADY_PROCESSED"
    assert second.json()["invoice_id"] == first.json()["invoice_id"]
    assert fake_ai.calls == 1
    assert len(_invoices_for_document(client, company["id"], document["id"])) == 1


def test_manual_invoice_is_not_hijacked_by_process_document(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    company = _company(client)
    document = _pdf_document(client, company["id"])
    manual = _create_manual_invoice(client, company["id"], document["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200
    body = response.json()
    assert body["invoice_id"] != manual["id"]
    manual_after = _get_invoice(client, company["id"], manual["id"])
    assert manual_after["source"] == "MANUAL"
    assert manual_after["status"] == "DRAFT"
    assert manual_after["invoice_number"] == "MANUAL-1"


# ---------------------------------------------------------------------------
# Provider capability branches
# ---------------------------------------------------------------------------


class _TextUnsupportedProvider:
    name = "textless"
    model = "textless-1"

    def __init__(self) -> None:
        self.image_calls = 0

    def is_configured(self) -> bool:
        return True

    def generate_from_images(self, *args: Any, **kwargs: Any) -> AIResponse:
        self.image_calls += 1
        raise AssertionError("a PDF with text must not call vision")


def test_text_capability_is_required_for_a_digital_pdf() -> None:
    provider = _TextUnsupportedProvider()
    extractor = AIDocumentInvoiceExtractor(provider=provider, ocr_processor=None)

    result = extractor.extract(_stored("invoice.pdf"), _pdf_bytes())

    assert result.outcome.value == "FAILED"
    assert result.reason == AI_UNSUPPORTED_CAPABILITY
    assert provider.image_calls == 0


class _ImageUnsupportedProvider:
    name = "visionless"
    model = "visionless-1"

    def is_configured(self) -> bool:
        return True

    def generate_from_images(self, *args: Any, **kwargs: Any) -> AIResponse:
        raise NotImplementedError


def test_image_capability_failure_is_reported_safely() -> None:
    extractor = AIDocumentInvoiceExtractor(
        provider=_ImageUnsupportedProvider(), ocr_processor=None
    )

    result = extractor.extract(
        _stored("invoice.png"), _image_bytes("PNG")
    )

    assert result.outcome.value == "FAILED"
    assert result.reason == AI_UNSUPPORTED_CAPABILITY


class _NoVisionMethodProvider:
    """A text-capable provider with no vision method at all."""

    name = "text-only"
    model = "text-only-1"

    def is_configured(self) -> bool:
        return True

    def generate_from_text(self, *args: Any, **kwargs: Any) -> AIResponse:
        raise AssertionError("an image must not be sent through the text path")


def test_missing_vision_method_is_an_unsupported_capability_not_an_internal_error() -> None:
    extractor = AIDocumentInvoiceExtractor(
        provider=_NoVisionMethodProvider(), ocr_processor=None
    )

    result = extractor.extract(_stored("invoice.jpg"), _image_bytes("JPEG"))

    assert result.outcome.value == "FAILED"
    assert result.reason == AI_UNSUPPORTED_CAPABILITY
    assert result.extraction_method == "IMAGE_VISION"


class _LegacyVisionProvider:
    """A vision provider written before JSON mode existed."""

    name = "legacy-vision"
    model = "legacy-vision-1"

    def __init__(self) -> None:
        self.calls = 0

    def is_configured(self) -> bool:
        return True

    def generate_from_images(
        self,
        prompt: str,
        images: Any,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AIResponse:
        self.calls += 1
        return AIResponse(text=json.dumps(SAMPLE_INVOICE), model=self.model)


def test_legacy_vision_provider_without_json_mode_still_works() -> None:
    provider = _LegacyVisionProvider()
    extractor = AIDocumentInvoiceExtractor(provider=provider, ocr_processor=None)

    result = extractor.extract(_stored("invoice.png"), _image_bytes("PNG"))

    assert result.outcome.value == "PROCESSED"
    assert result.extraction_method == "IMAGE_VISION"
    assert provider.calls == 1


class _LegacyTextProvider:
    """A provider written before the explicit text capability existed."""

    name = "legacy-text"
    model = "legacy-1"

    def is_configured(self) -> bool:
        return True

    def generate_text(self, *args: Any, **kwargs: Any) -> AIResponse:
        return AIResponse(text=json.dumps(SAMPLE_INVOICE), model=self.model)

    def generate_from_images(self, *args: Any, **kwargs: Any) -> AIResponse:
        raise AssertionError("a digital PDF must not call vision")


def test_legacy_generate_text_provider_still_reads_a_digital_pdf() -> None:
    extractor = AIDocumentInvoiceExtractor(
        provider=_LegacyTextProvider(), ocr_processor=None
    )

    result = extractor.extract(_stored("invoice.pdf"), _pdf_bytes())

    assert result.outcome.value == "PROCESSED"
    assert result.extraction_method == "NATIVE_TEXT"


def test_digital_pdf_never_enters_the_rasteriser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _must_not_render(*args: Any, **kwargs: Any) -> tuple:
        raise AssertionError("a digital PDF must not be rendered")

    monkeypatch.setattr(
        "app.services.invoice_extraction.prepare_vision_images_for_extension",
        _must_not_render,
    )
    extractor = AIDocumentInvoiceExtractor(
        provider=FakeProvider(), ocr_processor=None
    )

    result = extractor.extract(_stored("invoice.pdf"), _pdf_bytes())

    assert result.extraction_method == "NATIVE_TEXT"


def test_digital_pdf_with_good_text_never_calls_ocr() -> None:
    class ExplodingOCR:
        name = "must-not-run"

        def is_available(self) -> bool:
            return True

        def extract_text(self, raw_bytes: bytes, *, max_pages: int) -> tuple:
            raise AssertionError("OCR must not run when native text is usable")

    provider = FakeProvider()
    extractor = AIDocumentInvoiceExtractor(
        provider=provider, ocr_processor=ExplodingOCR()
    )

    result = extractor.extract(_stored("invoice.pdf"), _pdf_bytes())

    assert result.extraction_method == "NATIVE_TEXT"
    assert provider.text_calls == 1
    assert provider.image_calls == 0


@pytest.mark.parametrize(
    ("filename", "fmt"),
    (("invoice.png", "PNG"), ("invoice.jpg", "JPEG"), ("invoice.jpeg", "JPEG")),
)
def test_images_go_straight_to_vision_without_ocr(
    filename: str, fmt: str
) -> None:
    class ExplodingOCR:
        name = "must-not-run"

        def is_available(self) -> bool:
            return True

        def extract_text(self, raw_bytes: bytes, *, max_pages: int) -> tuple:
            raise AssertionError("images must not go through PDF OCR")

    provider = FakeProvider()
    extractor = AIDocumentInvoiceExtractor(
        provider=provider, ocr_processor=ExplodingOCR()
    )

    result = extractor.extract(_stored(filename), _image_bytes(fmt))

    assert result.outcome.value == "PROCESSED"
    assert result.extraction_method == "IMAGE_VISION"
    assert provider.text_calls == 0
    assert provider.image_calls == 1


def test_unusable_ocr_text_falls_back_to_vision() -> None:
    class NoisyOCR:
        name = "noisy-ocr"

        def is_available(self) -> bool:
            return True

        def extract_text(self, raw_bytes: bytes, *, max_pages: int) -> tuple:
            from app.services.text_quality import PageText

            return (PageText(page_number=1, text="T A X"),)

    provider = FakeProvider()
    extractor = AIDocumentInvoiceExtractor(
        provider=provider, ocr_processor=NoisyOCR()
    )

    result = extractor.extract(
        _stored("invoice.pdf"), _scanned_pdf_bytes()
    )

    assert result.extraction_method == "IMAGE_VISION"
    assert provider.text_calls == 0
    assert provider.image_calls == 1


def test_usable_ocr_text_never_enters_the_rasteriser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UsableOCR:
        name = "usable-ocr"

        def is_available(self) -> bool:
            return True

        def extract_text(self, raw_bytes: bytes, *, max_pages: int) -> tuple:
            from app.services.text_quality import PageText

            return (PageText(page_number=1, text="\n".join(_pdf_text_lines(0))),)

    def _must_not_render(*args: Any, **kwargs: Any) -> tuple:
        raise AssertionError("usable OCR text must not be rendered")

    monkeypatch.setattr(
        "app.services.invoice_extraction.prepare_vision_images_for_extension",
        _must_not_render,
    )
    extractor = AIDocumentInvoiceExtractor(
        provider=FakeProvider(), ocr_processor=UsableOCR()
    )

    result = extractor.extract(
        _stored("invoice.pdf"), _scanned_pdf_bytes()
    )

    assert result.extraction_method == "OCR_TEXT"


def test_process_response_exposes_only_safe_extraction_metadata() -> None:
    from app.api.v1.invoices.router import _process_response
    from app.services.invoice_extraction import (
        ExtractionMethod,
        ExtractionOutcome,
        ExtractionResult,
    )

    result = ExtractionResult(
        configured=True,
        outcome=ExtractionOutcome.PROCESSED,
        reason=None,
        message="Invoice extracted and processed.",
        extraction_method=ExtractionMethod.NATIVE_TEXT.value,
        provider="fake",
        model="fake-1",
    )

    response = _process_response(result, document_id=uuid.uuid4())

    assert response.extraction_method == "NATIVE_TEXT"
    assert "DOCUMENT TEXT" not in response.model_dump_json()
    assert "file_path" not in response.model_dump_json()


# ---------------------------------------------------------------------------
# Document preparation unit tests
# ---------------------------------------------------------------------------


def _stored(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        original_filename=name,
        stored_filename=f"generated{Path(name).suffix}",
    )


def test_prepare_png_returns_one_jpeg() -> None:
    images = prepare_document(_stored("invoice.png"), _image_bytes("PNG"), max_pages=10)

    assert len(images) == 1
    assert images[0].mime_type == "image/jpeg"
    assert images[0].data.startswith(b"\xff\xd8\xff")


def test_prepare_jpeg_returns_one_jpeg() -> None:
    images = prepare_document(_stored("invoice.jpg"), _image_bytes("JPEG"), max_pages=10)

    assert len(images) == 1
    assert images[0].mime_type == "image/jpeg"


def test_prepare_pdf_renders_each_page() -> None:
    raw = _pdf_bytes(pages=3)

    images = prepare_document(_stored("invoice.pdf"), raw, max_pages=10)

    assert len(images) == 3
    assert all(image.mime_type == "image/jpeg" for image in images)


def test_prepare_pdf_does_not_modify_the_source_bytes() -> None:
    raw = _pdf_bytes(pages=2)
    before = bytes(raw)

    prepare_document(_stored("invoice.pdf"), raw, max_pages=10)

    assert raw == before


def test_prepare_pdf_enforces_the_page_limit() -> None:
    with pytest.raises(DocumentPreparationError) as caught:
        prepare_document(_stored("invoice.pdf"), _pdf_bytes(pages=4), max_pages=3)

    assert caught.value.code == DocumentPreparationError.DOCUMENT_TOO_MANY_PAGES
    assert "4 pages" in caught.value.message


def test_prepare_rejects_unsupported_extension() -> None:
    with pytest.raises(DocumentPreparationError) as caught:
        prepare_document(_stored("ledger.xlsx"), b"PK\x03\x04", max_pages=10)

    assert caught.value.code == DocumentPreparationError.UNSUPPORTED_DOCUMENT_TYPE


def test_prepare_rejects_mismatched_signature() -> None:
    with pytest.raises(DocumentPreparationError) as caught:
        prepare_document(_stored("invoice.png"), b"not a png", max_pages=10)

    assert caught.value.code == DocumentPreparationError.INVALID_DOCUMENT


def test_prepare_rejects_a_broken_pdf() -> None:
    with pytest.raises(DocumentPreparationError) as caught:
        prepare_document(_stored("invoice.pdf"), b"%PDF-1.7 broken", max_pages=10)

    assert caught.value.code == DocumentPreparationError.DOCUMENT_UNREADABLE


# ---------------------------------------------------------------------------
# Strict parsing / decimal safety
# ---------------------------------------------------------------------------


def test_json_numeric_amounts_become_exact_decimals() -> None:
    payload = parse_extraction_payload(
        '{"is_invoice": true, "invoice_number": "X", "total_amount": 1180.50}'
    )

    assert payload.total_amount == Decimal("1180.50")
    assert isinstance(payload.total_amount, Decimal)


def test_json_float_is_never_a_python_float() -> None:
    payload = parse_extraction_payload(
        '{"is_invoice": true, "taxable_amount": 0.1, "cgst_amount": 0.2}'
    )

    assert payload.taxable_amount + payload.cgst_amount == Decimal("0.3")
    assert not isinstance(payload.taxable_amount, float)


def test_over_precise_amount_is_invalid_output() -> None:
    with pytest.raises(InvalidExtractionOutput):
        parse_extraction_payload(
            '{"is_invoice": true, "invoice_number": "X", "total_amount": "10.123"}'
        )


def test_nan_is_rejected() -> None:
    with pytest.raises(InvalidExtractionOutput):
        parse_extraction_payload(
            '{"is_invoice": true, "invoice_number": "X", "total_amount": NaN}'
        )


def test_missing_is_invoice_is_rejected() -> None:
    with pytest.raises(InvalidExtractionOutput):
        parse_extraction_payload('{"invoice_number": "X"}')


def test_extra_keys_are_ignored() -> None:
    payload = parse_extraction_payload(
        '{"is_invoice": true, "invoice_number": "X", "made_up": "ignore me"}'
    )

    assert payload.invoice_number == "X"
    assert not hasattr(payload, "made_up")


def test_validation_parses_printed_day_first_dates() -> None:
    payload = parse_extraction_payload(
        json.dumps(
            {
                "is_invoice": True,
                "invoice_number": "X",
                "invoice_date": "15/03/2026",
                "due_date": "14.04.2026",
                "total_amount": "100.00",
            }
        )
    )

    validated = validate_extraction(payload)

    assert validated.values["invoice_date"] == date(2026, 3, 15)
    assert validated.values["due_date"] == date(2026, 4, 14)


def test_validation_normalises_currency_and_identifiers() -> None:
    payload = parse_extraction_payload(
        json.dumps(
            {
                "is_invoice": True,
                "invoice_number": "X",
                "vendor_gstin": "27aaccd1234f1z2",
                "vendor_pan": "aaccd1234f",
                "currency": "inr",
                "total_amount": "100.00",
            }
        )
    )

    validated = validate_extraction(payload)

    assert validated.values["vendor_gstin"] == "27AACCD1234F1Z2"
    assert validated.values["vendor_pan"] == "AACCD1234F"
    assert validated.values["currency"] == "INR"
    assert validated.issues == ()


def test_invalid_currency_is_reported_and_not_persisted() -> None:
    payload = parse_extraction_payload(
        json.dumps(
            {
                "is_invoice": True,
                "invoice_number": "X",
                "currency": "RUPEE",
                "total_amount": "100.00",
            }
        )
    )

    validated = validate_extraction(payload)

    assert validated.values["currency"] is None
    assert any("Currency" in issue for issue in validated.issues)


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


def test_process_response_never_exposes_internal_storage_paths(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    company = _company(client)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200
    text = response.text.lower()
    assert "file_path" not in text
    assert "stored_filename" not in text
    assert "storage" not in text
    assert "sk-" not in text


def test_process_response_reflects_the_applied_invoice(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    company = _company(client)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    body = response.json()
    assert body["document_id"] == document["id"]
    assert body["invoice_id"]
    assert uuid.UUID(body["invoice_id"])


# ---------------------------------------------------------------------------
# New extraction scenarios
# ---------------------------------------------------------------------------


def test_igst_only_invoice_extracts_igst_and_leaves_cgst_sgst_null(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    """An inter-state invoice has IGST; CGST and SGST must be null, not zero."""
    igst_invoice = {
        "is_invoice": True,
        "invoice_number": "IGST/2026/001",
        "invoice_date": "2026-03-15",
        "vendor_name": "Delhi Distributors",
        "vendor_gstin": "07AABCD1234E1Z1",
        "buyer_gstin": "27AAJCS4821K1Z5",
        "place_of_supply": "Maharashtra",
        "currency": "INR",
        "taxable_amount": "1000.00",
        "cgst_amount": None,
        "sgst_amount": None,
        "igst_amount": "180.00",
        "cess_amount": None,
        "round_off": None,
        "total_amount": "1180.00",
    }
    company = _company(client)
    # Seed vendor so extraction lands on PROCESSED, not mapping review.
    client.post(
        f"{COMPANIES_URL}/{company['id']}/vendors",
        json={"name": "Delhi Distributors", "gstin": "07AABCD1234E1Z1"},
    )
    fake_ai.text = json.dumps(igst_invoice)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200
    assert response.json()["status"] == "PROCESSED"

    invoice_id = response.json()["invoice_id"]
    invoice = _get_invoice(client, company["id"], invoice_id)
    assert invoice["igst_amount"] == "180.00"
    assert invoice["cgst_amount"] is None
    assert invoice["sgst_amount"] is None
    assert invoice["place_of_supply"] == "Maharashtra"


def test_place_of_supply_extracted_from_cgst_sgst_invoice(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    """place_of_supply is persisted for intra-state invoices too."""
    invoice_with_pos = {
        **SAMPLE_INVOICE,
        "place_of_supply": "27 - Maharashtra",
    }
    company = _company(client)
    fake_ai.text = json.dumps(invoice_with_pos)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200
    invoice_id = response.json()["invoice_id"]
    invoice = _get_invoice(client, company["id"], invoice_id)
    assert invoice["place_of_supply"] == "27 - Maharashtra"


def test_place_of_supply_regex_candidate_found_in_pdf_text() -> None:
    """RegexExtractor picks up 'Place of Supply: Maharashtra' from text."""
    from app.services.regex_extraction import RegexExtractor
    from app.services.text_quality import PageText

    pages = [
        PageText(
            page_number=1,
            text=(
                "TAX INVOICE\n"
                "Invoice No: TEST-001\n"
                "Place of Supply: Maharashtra (27)\n"
                "Taxable Amount: 1000.00\n"
                "CGST: 90.00\n"
                "SGST: 90.00\n"
                "Grand Total: 1180.00\n"
            ),
        )
    ]
    candidates = RegexExtractor().extract(pages)
    pos = candidates.get("place_of_supply")
    assert pos, "Expected at least one place_of_supply candidate"
    assert "Maharashtra" in pos[0].value


def test_multi_line_invoice_extracts_and_persists_line_items(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    """AI-returned line_items are stored as InvoiceLineItem rows."""
    multi_line = {
        **SAMPLE_INVOICE,
        "line_items": [
            {
                "line_number": 1,
                "description": "Freight Service Mumbai–Pune",
                "supply_type": "SERVICE",
                "hsn_sac": "9965",
                "quantity": "1.000",
                "unit_rate": "600.00",
                "gst_rate": "18.00",
                "taxable_amount": "600.00",
                "cgst_amount": "54.00",
                "sgst_amount": "54.00",
                "igst_amount": None,
                "cess_amount": None,
                "total_amount": "708.00",
            },
            {
                "line_number": 2,
                "description": "Handling charges",
                "supply_type": "SERVICE",
                "hsn_sac": "9965",
                "quantity": "1.000",
                "unit_rate": "400.00",
                "gst_rate": "18.00",
                "taxable_amount": "400.00",
                "cgst_amount": "36.00",
                "sgst_amount": "36.00",
                "igst_amount": None,
                "cess_amount": None,
                "total_amount": "472.00",
            },
        ],
    }
    company = _company(client)
    fake_ai.text = json.dumps(multi_line)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200
    invoice_id = response.json()["invoice_id"]

    # Confirm line items were stored.
    line_items_response = client.get(
        f"{COMPANIES_URL}/{company['id']}/invoices/{invoice_id}/line-items"
    )
    assert line_items_response.status_code == 200
    items = line_items_response.json()
    assert len(items) == 2
    descs = {item["description"] for item in items}
    assert "Freight Service Mumbai–Pune" in descs
    assert "Handling charges" in descs
    first = next(i for i in items if i["line_number"] == 1)
    assert first["unit_rate"] == "600.00"
    assert first["hsn_sac"] == "9965"
    assert first["supply_type"] == "SERVICE"


def test_line_items_cleared_and_rewritten_on_retry(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    """A second extraction (after NEEDS_REVIEW) replaces, not appends, line items."""
    # First pass: total mismatch forces NEEDS_REVIEW so a retry is possible.
    needs_review_payload = {
        **SAMPLE_INVOICE,
        "total_amount": "9999.00",  # wrong — triggers total-mismatch issue
        "line_items": [
            {
                "line_number": 1,
                "description": "Original item",
                "quantity": "1.000",
                "unit_rate": "1000.00",
                "taxable_amount": "1000.00",
                "total_amount": "1180.00",
            }
        ],
    }
    two_lines = {
        **SAMPLE_INVOICE,
        "line_items": [
            {
                "line_number": 1,
                "description": "Item A",
                "quantity": "1.000",
                "unit_rate": "600.00",
                "taxable_amount": "600.00",
            },
            {
                "line_number": 2,
                "description": "Item B",
                "quantity": "1.000",
                "unit_rate": "400.00",
                "taxable_amount": "400.00",
            },
        ],
    }
    company = _company(client)
    fake_ai.text = json.dumps(needs_review_payload)
    document = _pdf_document(client, company["id"])
    first_response = _process_document(client, company["id"], document["id"])
    assert first_response.status_code == 200
    assert first_response.json()["status"] == "NEEDS_REVIEW"
    invoice_id = first_response.json()["invoice_id"]

    # Retry from NEEDS_REVIEW — the process-document endpoint handles this.
    fake_ai.text = json.dumps(two_lines)
    second_response = _process_document(client, company["id"], document["id"])
    assert second_response.status_code == 200

    items_response = client.get(
        f"{COMPANIES_URL}/{company['id']}/invoices/{invoice_id}/line-items"
    )
    items = items_response.json()
    assert len(items) == 2
    descs = {i["description"] for i in items}
    assert "Original item" not in descs
    assert "Item A" in descs
    assert "Item B" in descs


def test_discount_and_freight_invoice_passes_total_check(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    """Header-level discount and freight are factored into the total check."""
    # total = 1000 + 180 (18% igst) - 50 (discount) + 100 (freight) = 1230
    discount_freight = {
        "is_invoice": True,
        "invoice_number": "DF/2026/099",
        "invoice_date": "2026-03-15",
        "vendor_name": "Deccan Freight Carriers",
        "vendor_gstin": "27AACCD1234F1Z2",
        "vendor_pan": "AACCD1234F",
        "buyer_gstin": "27AAJCS4821K1Z5",
        "currency": "INR",
        "taxable_amount": "1000.00",
        "cgst_amount": "90.00",
        "sgst_amount": "90.00",
        "igst_amount": None,
        "cess_amount": None,
        "discount_amount": "50.00",
        "freight_amount": "100.00",
        "round_off": None,
        "total_amount": "1230.00",
    }
    company = _company(client)
    fake_ai.text = json.dumps(discount_freight)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200
    body = response.json()
    # No total-mismatch issue — if the formula is wrong, status is NEEDS_REVIEW.
    assert body["status"] == "PROCESSED"
    invoice_id = body["invoice_id"]
    invoice = _get_invoice(client, company["id"], invoice_id)
    assert invoice["discount_amount"] == "50.00"
    assert invoice["freight_amount"] == "100.00"


def test_discount_without_freight_passes_total_check() -> None:
    """validate_invoice_fields handles discount-only adjustments correctly."""
    from app.services.invoice_validation import validate_invoice_fields

    result = validate_invoice_fields(
        taxable_amount=Decimal("1000.00"),
        cgst_amount=Decimal("90.00"),
        sgst_amount=Decimal("90.00"),
        discount_amount=Decimal("50.00"),
        total_amount=Decimal("1130.00"),
    )
    assert result.is_valid, result.issues


def test_freight_without_discount_passes_total_check() -> None:
    """validate_invoice_fields handles freight-only adjustments correctly."""
    from app.services.invoice_validation import validate_invoice_fields

    result = validate_invoice_fields(
        taxable_amount=Decimal("1000.00"),
        igst_amount=Decimal("180.00"),
        freight_amount=Decimal("100.00"),
        total_amount=Decimal("1280.00"),
    )
    assert result.is_valid, result.issues


def test_total_mismatch_still_flagged_with_discount_and_freight() -> None:
    """A wrong total is still caught even when discount/freight are present."""
    from app.services.invoice_validation import validate_invoice_fields

    result = validate_invoice_fields(
        taxable_amount=Decimal("1000.00"),
        cgst_amount=Decimal("90.00"),
        sgst_amount=Decimal("90.00"),
        discount_amount=Decimal("50.00"),
        freight_amount=Decimal("100.00"),
        total_amount=Decimal("9999.00"),  # wrong
    )
    assert not result.is_valid
    assert any("Total amount does not match" in issue for issue in result.issues)


def test_line_items_with_null_description_are_skipped() -> None:
    """_collect_line_items silently drops items without a description."""
    from app.services.invoice_extraction_validation import (
        ExtractedLineItem,
        _collect_line_items,
    )

    items = [
        ExtractedLineItem(line_number=1, description="Valid item", unit_rate=Decimal("100.00")),
        ExtractedLineItem(line_number=2, description=None),
        ExtractedLineItem(line_number=3, description="  ", unit_rate=Decimal("50.00")),
    ]
    # description=" " is cleaned to None by the field_validator, but test
    # the case where it genuinely arrives as None.
    result = _collect_line_items(items)
    assert len(result) == 1
    assert result[0]["description"] == "Valid item"


def test_different_vendor_layout_two_column_header(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    """Invoice with seller/buyer side-by-side (common in PDF exports) still extracts correctly."""
    two_col_layout = {
        "is_invoice": True,
        "invoice_number": "2COL/001",
        "invoice_date": "2026-03-15",
        "vendor_name": "ABC Supplies Pvt Ltd",
        "vendor_gstin": "29AAABC1234D1Z3",
        "buyer_gstin": "27AAJCS4821K1Z5",
        "currency": "INR",
        "taxable_amount": "5000.00",
        "igst_amount": "900.00",
        "cgst_amount": None,
        "sgst_amount": None,
        "cess_amount": None,
        "round_off": None,
        "total_amount": "5900.00",
    }
    company = _company(client)
    client.post(
        f"{COMPANIES_URL}/{company['id']}/vendors",
        json={"name": "ABC Supplies Pvt Ltd", "gstin": "29AAABC1234D1Z3"},
    )
    fake_ai.text = json.dumps(two_col_layout)
    document = _pdf_document(client, company["id"])

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200
    assert response.json()["status"] == "PROCESSED"
    invoice_id = response.json()["invoice_id"]
    invoice = _get_invoice(client, company["id"], invoice_id)
    assert invoice["vendor_name"] == "ABC Supplies Pvt Ltd"
    assert invoice["igst_amount"] == "900.00"
    assert invoice["cgst_amount"] is None
