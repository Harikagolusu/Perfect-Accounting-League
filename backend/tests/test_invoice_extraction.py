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
from app.services.invoice_extraction_validation import (
    InvalidExtractionOutput,
    parse_extraction_payload,
    validate_extraction,
)

COMPANIES_URL = "/api/v1/companies"

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
        self.images: tuple[Any, ...] = ()
        self.json_mode: bool | None = None
        self.system_prompt: str | None = None

    def is_configured(self) -> bool:
        return self.configured

    def generate_text(self, *args: Any, **kwargs: Any) -> AIResponse:
        raise NotImplementedError

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
        self.images = tuple(images)
        self.json_mode = json_mode
        self.system_prompt = system_prompt
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
    return response.json()


def _pdf_bytes(pages: int = 1) -> bytes:
    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page()
        page.insert_text((72, 72), f"TAX INVOICE - page {index + 1}")
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
    assert "file_path" not in body

    invoice = _get_invoice(client, company["id"], body["invoice_id"])
    assert invoice["source"] == "AI"
    assert invoice["status"] == "PROCESSED"
    assert invoice["vendor_id"] is None
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
    assert fake_ai.json_mode is True
    assert len(fake_ai.images) == 1
    assert fake_ai.images[0].mime_type == "image/jpeg"
    assert fake_ai.system_prompt and "Never invent" in fake_ai.system_prompt


def test_image_document_is_accepted(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    company = _company(client)
    document = _image_document(client, company["id"], "PNG")

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "PROCESSED"
    assert len(fake_ai.images) == 1


def test_multi_page_pdf_sends_every_page(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    company = _company(client)
    document = _pdf_document(client, company["id"], pages=3)

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    assert len(fake_ai.images) == 3


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


def test_unsupported_document_type_fails_without_calling_ai(
    client: TestClient, fake_ai: FakeProvider
) -> None:
    company = _company(client)
    document = _upload(
        client,
        company["id"],
        filename="ledger.xlsx",
        data=b"PK\x03\x04" + b"0" * 40,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["reason"] == "UNSUPPORTED_DOCUMENT_TYPE"
    assert "PDF, JPEG and PNG" in body["message"]
    assert fake_ai.calls == 0


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
