"""Tests for the Invoice Processing foundation.

Two things matter most here: money never touches binary floating point, and
an invoice can only ever be reached through the company that owns it — and can
only point at documents and vendors of that same company.
"""

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.invoice import InvoiceStatus
from app.models.vendor import Vendor
from app.services.invoice import get_invoice

COMPANIES_URL = "/api/v1/companies"
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"

VALID: dict[str, Any] = {
    "invoice_number": "INV-2026-001",
    "invoice_date": "2026-03-15",
    "due_date": "2026-04-14",
    "vendor_name": "Deccan Freight Carriers",
    "vendor_gstin": "27AACCD1234F1Z2",
    "vendor_pan": "AACCD1234F",
    "buyer_gstin": "27AAJCS4821K1Z5",
    "po_number": "PO-8891",
    "taxable_amount": "1000.00",
    "cgst_amount": "90.00",
    "sgst_amount": "90.00",
    "cess_amount": "0.00",
    "round_off": "0.00",
    "total_amount": "1180.00",
}


def _company(client: TestClient, name: str = "Test Co") -> dict[str, Any]:
    response = client.post(COMPANIES_URL, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _document(client: TestClient, company_id: str, name: str = "invoice.pdf") -> dict[str, Any]:
    response = client.post(
        f"{COMPANIES_URL}/{company_id}/documents",
        files={"file": (name, PDF_BYTES, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _vendor(client: TestClient, company_id: str, name: str = "A Vendor") -> dict[str, Any]:
    response = client.post(
        f"{COMPANIES_URL}/{company_id}/vendors",
        json={"name": name, "pan": "AACCD1234F", "gstin": "27AACCD1234F1Z2"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _invoices_url(company_id: Any) -> str:
    return f"{COMPANIES_URL}/{company_id}/invoices"


def _create(
    client: TestClient,
    company_id: str,
    document_id: str,
    **overrides: Any,
) -> Any:
    return client.post(
        _invoices_url(company_id),
        json={**VALID, "document_id": document_id, **overrides},
    )


@pytest.fixture
def company(client: TestClient) -> dict[str, Any]:
    return _company(client)


@pytest.fixture
def ready(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    """A company with one document ready to invoice."""
    co = _company(client)
    doc = _document(client, co["id"])
    return co, doc


@pytest.fixture
def two_companies(
    client: TestClient,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Company A with invoice A1, Company B with invoice B1."""
    a, b = _company(client, "Company A"), _company(client, "Company B")
    a_doc, b_doc = _document(client, a["id"], "invoice-a.pdf"), _document(
        client, b["id"], "invoice-b.pdf"
    )
    assert _create(client, a["id"], a_doc["id"], invoice_number="A-1").status_code == 201
    assert _create(client, b["id"], b_doc["id"], invoice_number="B-1").status_code == 201
    return a, b, a_doc, b_doc


# --- Create ----------------------------------------------------------------


def test_create_invoice_returns_the_record(ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient) -> None:
    co, doc = ready

    response = _create(client, co["id"], doc["id"])

    assert response.status_code == 201, response.text
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["company_id"] == co["id"]
    assert body["document_id"] == doc["id"]
    assert body["invoice_number"] == "INV-2026-001"
    assert body["status"] == "DRAFT"
    # Nothing has read the file, so the source is honestly manual.
    assert body["source"] == "MANUAL"
    assert body["created_at"] and body["updated_at"]


def test_create_invoice_links_a_vendor_of_the_same_company(
    client: TestClient,
) -> None:
    co = _company(client)
    doc = _document(client, co["id"])
    vendor = _vendor(client, co["id"])

    response = _create(client, co["id"], doc["id"], vendor_id=vendor["id"])

    assert response.status_code == 201
    assert response.json()["vendor_id"] == vendor["id"]


def test_create_rejects_nonexistent_document(client: TestClient, company: dict[str, Any]) -> None:
    response = _create(client, company["id"], str(uuid.uuid4()))

    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found"


def test_create_rejects_document_from_another_company(client: TestClient) -> None:
    a = _company(client, "Company A")
    b = _company(client, "Company B")
    b_doc = _document(client, b["id"])

    response = _create(client, a["id"], b_doc["id"])

    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found"


def test_create_rejects_vendor_from_another_company(client: TestClient) -> None:
    a = _company(client, "Company A")
    b = _company(client, "Company B")
    a_doc = _document(client, a["id"])
    b_vendor = _vendor(client, b["id"], "B only Vendor")

    response = _create(client, a["id"], a_doc["id"], vendor_id=b_vendor["id"])

    assert response.status_code == 404
    assert response.json()["detail"] == "Vendor not found"


def test_create_rejects_unknown_company(client: TestClient) -> None:
    response = client.post(
        _invoices_url(uuid.uuid4()), json={**VALID, "document_id": str(uuid.uuid4())}
    )

    assert response.status_code == 404


def test_create_rejects_company_id_in_the_body(
    client: TestClient, ready: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    co, doc = ready

    response = client.post(
        _invoices_url(co["id"]),
        json={**VALID, "document_id": doc["id"], "company_id": str(uuid.uuid4())},
    )

    assert response.status_code == 422


def test_create_requires_a_document(ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient) -> None:
    co, _ = ready

    response = client.post(_invoices_url(co["id"]), json={k: v for k, v in VALID.items()})

    assert response.status_code == 422


# --- Money -----------------------------------------------------------------


def test_money_is_decimal_not_float(ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient) -> None:
    """0.1 + 0.2 must be 0.30, not 0.30000000000000004."""
    co, doc = ready

    body = _create(
        client,
        co["id"],
        doc["id"],
        taxable_amount="0.10",
        cgst_amount="0.20",
        sgst_amount="0.00",
        cess_amount="0.00",
        round_off="0.00",
        total_amount="0.30",
    ).json()

    assert body["total_amount"] == "0.30"
    assert body["taxable_amount"] == "0.10"


def test_money_round_trips_exactly(ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient, db: Session) -> None:
    co, doc = ready

    created = _create(
        client,
        co["id"],
        doc["id"],
        taxable_amount="1180.50",
        total_amount="1180.50",
    ).json()

    row = get_invoice(db, uuid.UUID(co["id"]), uuid.UUID(created["id"]))
    assert isinstance(row.total_amount, Decimal)
    assert row.total_amount == Decimal("1180.50")
    # Not a float: a float round-trip would lose the trailing zero.
    assert str(row.total_amount) == "1180.50"


def test_rejects_negative_amounts(ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient) -> None:
    co, doc = ready

    response = _create(client, co["id"], doc["id"], taxable_amount="-1.00")

    assert response.status_code == 422


def test_rejects_over_precision_amounts(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    co, doc = ready

    response = _create(client, co["id"], doc["id"], taxable_amount="1.005")

    assert response.status_code == 422


# --- Dates -----------------------------------------------------------------


def test_dates_round_trip_as_dates(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient, db: Session
) -> None:
    co, doc = ready

    body = _create(client, co["id"], doc["id"]).json()
    assert body["invoice_date"] == "2026-03-15"
    assert body["due_date"] == "2026-04-14"

    row = get_invoice(db, uuid.UUID(co["id"]), uuid.UUID(body["id"]))
    assert row.invoice_date is not None
    assert row.invoice_date.year == 2026
    assert row.invoice_date.month == 3
    assert row.invoice_date.day == 15


def test_rejects_malformed_date(ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient) -> None:
    co, doc = ready

    response = _create(client, co["id"], doc["id"], invoice_date="15-03-2026")

    assert response.status_code == 422


def test_due_date_before_invoice_date_is_reported_not_fixed(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    """An inconsistency is surfaced for review; the value is never rewritten."""
    co, doc = ready

    response = _create(client, co["id"], doc["id"], due_date="2026-01-01")

    assert response.status_code == 201
    # Stored exactly as entered.
    assert response.json()["due_date"] == "2026-01-01"


# --- Normalisation ---------------------------------------------------------


def test_blank_optional_fields_normalise_to_null(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    co, doc = ready

    body = _create(
        client,
        co["id"],
        doc["id"],
        po_number="   ",
        vendor_name="",
        buyer_gstin="",
        total_amount="1180.00",
    ).json()

    assert body["po_number"] is None
    assert body["vendor_name"] is None
    assert body["buyer_gstin"] is None


def test_tax_identifiers_are_upper_cased(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    co, doc = ready

    body = _create(
        client, co["id"], doc["id"], vendor_gstin=" 27aaccd1234f1z2 ", vendor_pan=" aaccd1234f "
    ).json()

    assert body["vendor_gstin"] == "27AACCD1234F1Z2"
    assert body["vendor_pan"] == "AACCD1234F"


def test_invoice_number_whitespace_is_trimmed_but_otherwise_preserved(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    """Invoice identifiers carry meaningful formatting; only edges are cut."""
    co, doc = ready

    body = _create(client, co["id"], doc["id"], invoice_number="  INV/2026-001  ").json()

    assert body["invoice_number"] == "INV/2026-001"


# --- Get / List ------------------------------------------------------------


def test_get_invoice(ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient) -> None:
    co, doc = ready
    created = _create(client, co["id"], doc["id"]).json()

    response = client.get(f"{_invoices_url(co['id'])}/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_get_nonexistent_invoice_returns_404(ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient) -> None:
    co, _ = ready

    response = client.get(f"{_invoices_url(co['id'])}/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Invoice not found"


def test_list_invoices(ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient) -> None:
    co, doc = ready
    _create(client, co["id"], doc["id"], invoice_number="INV-1")

    response = client.get(_invoices_url(co["id"]))

    assert response.status_code == 200
    assert [i["invoice_number"] for i in response.json()] == ["INV-1"]


def test_list_is_empty_for_a_company_with_none(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    co, _ = ready

    response = client.get(_invoices_url(co["id"]))

    assert response.status_code == 200
    assert response.json() == []


def test_list_filters_by_status(ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient) -> None:
    co, doc = ready
    _create(client, co["id"], doc["id"], invoice_number="INV-1")

    assert len(client.get(_invoices_url(co["id"]), params={"status": "DRAFT"}).json()) == 1
    assert client.get(_invoices_url(co["id"]), params={"status": "PROCESSED"}).json() == []


def test_list_filters_by_document(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    co, doc = ready
    _create(client, co["id"], doc["id"])

    matched = client.get(_invoices_url(co["id"]), params={"document_id": doc["id"]})
    unmatched = client.get(_invoices_url(co["id"]), params={"document_id": str(uuid.uuid4())})

    assert len(matched.json()) == 1
    assert unmatched.json() == []


def test_list_rejects_invalid_filter(ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient) -> None:
    co, _ = ready

    assert client.get(_invoices_url(co["id"]), params={"status": "NOPE"}).status_code == 422


# --- Update ----------------------------------------------------------------


def test_update_invoice(ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient) -> None:
    co, doc = ready
    created = _create(client, co["id"], doc["id"]).json()

    response = client.patch(
        f"{_invoices_url(co['id'])}/{created['id']}", json={"invoice_number": "INV-EDITED"}
    )

    assert response.status_code == 200
    assert response.json()["invoice_number"] == "INV-EDITED"


def test_partial_update_touches_only_supplied_fields(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    co, doc = ready
    created = _create(client, co["id"], doc["id"]).json()

    response = client.patch(
        f"{_invoices_url(co['id'])}/{created['id']}", json={"total_amount": "1200.00"}
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["total_amount"] == "1200.00"
    assert updated["invoice_number"] == created["invoice_number"]
    assert updated["created_at"] == created["created_at"]


# --- Status lifecycle ------------------------------------------------------


def test_valid_status_transition_is_accepted(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    co, doc = ready
    created = _create(client, co["id"], doc["id"]).json()

    response = client.patch(
        f"{_invoices_url(co['id'])}/{created['id']}", json={"status": "NEEDS_REVIEW"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "NEEDS_REVIEW"


def test_invalid_status_transition_is_rejected(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    """DRAFT cannot jump straight to PROCESSED: it has to be processed."""
    co, doc = ready
    created = _create(client, co["id"], doc["id"]).json()

    response = client.patch(
        f"{_invoices_url(co['id'])}/{created['id']}", json={"status": "PROCESSED"}
    )

    assert response.status_code == 409
    assert "Cannot move an invoice from DRAFT" in response.json()["detail"]


def test_failed_cannot_jump_to_processed(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    co, doc = ready
    created = _create(client, co["id"], doc["id"]).json()
    client.patch(f"{_invoices_url(co['id'])}/{created['id']}", json={"status": "PROCESSING"})
    client.patch(f"{_invoices_url(co['id'])}/{created['id']}", json={"status": "FAILED"})

    response = client.patch(
        f"{_invoices_url(co['id'])}/{created['id']}", json={"status": "PROCESSED"}
    )

    assert response.status_code == 409
    assert client.get(f"{_invoices_url(co['id'])}/{created['id']}").json()["status"] == "FAILED"


def test_processed_is_terminal(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    co, doc = ready
    created = _create(client, co["id"], doc["id"]).json()
    client.patch(f"{_invoices_url(co['id'])}/{created['id']}", json={"status": "PROCESSING"})
    client.patch(f"{_invoices_url(co['id'])}/{created['id']}", json={"status": "PROCESSED"})

    response = client.patch(
        f"{_invoices_url(co['id'])}/{created['id']}", json={"status": "DRAFT"}
    )

    assert response.status_code == 409


# --- Delete ----------------------------------------------------------------


def test_delete_invoice(ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient) -> None:
    co, doc = ready
    created = _create(client, co["id"], doc["id"]).json()

    response = client.delete(f"{_invoices_url(co['id'])}/{created['id']}")

    assert response.status_code == 204
    assert client.get(f"{_invoices_url(co['id'])}/{created['id']}").status_code == 404
    assert client.get(_invoices_url(co["id"])).json() == []


def test_delete_keeps_the_source_document(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    """The uploaded file is the artifact of record and must survive."""
    co, doc = ready
    created = _create(client, co["id"], doc["id"]).json()

    client.delete(f"{_invoices_url(co['id'])}/{created['id']}")

    still_there = client.get(f"{COMPANIES_URL}/{co['id']}/documents/{doc['id']}")
    assert still_there.status_code == 200
    assert still_there.json()["id"] == doc["id"]


def test_delete_nonexistent_invoice_returns_404(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    co, _ = ready

    assert client.delete(f"{_invoices_url(co['id'])}/{uuid.uuid4()}").status_code == 404


# --- Company isolation -----------------------------------------------------


def test_companies_do_not_see_each_others_invoices(
    two_companies: tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]],
    client: TestClient,
) -> None:
    a, b, _, _ = two_companies

    listed_a = client.get(_invoices_url(a["id"])).json()
    listed_b = client.get(_invoices_url(b["id"])).json()

    assert [i["invoice_number"] for i in listed_a] == ["A-1"]
    assert [i["invoice_number"] for i in listed_b] == ["B-1"]


def test_cross_company_get_returns_404(
    two_companies: tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]],
    client: TestClient,
) -> None:
    a, b, _, _ = two_companies
    b_invoice = client.get(_invoices_url(b["id"])).json()[0]

    assert client.get(f"{_invoices_url(a['id'])}/{b_invoice['id']}").status_code == 404


def test_cross_company_patch_returns_404(
    two_companies: tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]],
    client: TestClient,
) -> None:
    a, b, _, _ = two_companies
    b_invoice = client.get(_invoices_url(b["id"])).json()[0]

    response = client.patch(
        f"{_invoices_url(a['id'])}/{b_invoice['id']}", json={"invoice_number": "Hijacked"}
    )

    assert response.status_code == 404
    assert client.get(f"{_invoices_url(b['id'])}/{b_invoice['id']}").json()["invoice_number"] == "B-1"


def test_cross_company_delete_returns_404(
    two_companies: tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]],
    client: TestClient,
) -> None:
    a, b, _, _ = two_companies
    b_invoice = client.get(_invoices_url(b["id"])).json()[0]

    response = client.delete(f"{_invoices_url(a['id'])}/{b_invoice['id']}")

    assert response.status_code == 404
    assert client.get(f"{_invoices_url(b['id'])}/{b_invoice['id']}").status_code == 200


# --- Processing ------------------------------------------------------------


def test_processing_reports_that_extraction_is_not_configured(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    co, doc = ready
    created = _create(client, co["id"], doc["id"]).json()

    response = client.post(f"{_invoices_url(co['id'])}/{created['id']}/process")

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["reason"] == "EXTRACTION_NOT_CONFIGURED"
    assert "not configured" in body["message"].lower()


def test_processing_invents_no_invoice_values(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    """No fabricated invoice number, vendor or amount may appear."""
    co, doc = ready
    created = _create(client, co["id"], doc["id"]).json()

    response = client.post(f"{_invoices_url(co['id'])}/{created['id']}/process")

    for fabricated in ("invoice_number", "vendor_name", "total_amount", "values"):
        assert fabricated not in response.json()


def test_processing_leaves_the_document_status_alone(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    """Nothing was processed, so claiming PROCESSING or FAILED would be false."""
    co, doc = ready
    created = _create(client, co["id"], doc["id"]).json()

    client.post(f"{_invoices_url(co['id'])}/{created['id']}/process")

    document = client.get(f"{COMPANIES_URL}/{co['id']}/documents/{doc['id']}").json()
    assert document["status"] == "UPLOADED"


def test_processing_leaves_the_invoice_status_alone(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    co, doc = ready
    created = _create(client, co["id"], doc["id"]).json()

    client.post(f"{_invoices_url(co['id'])}/{created['id']}/process")

    assert client.get(f"{_invoices_url(co['id'])}/{created['id']}").json()["status"] == "DRAFT"


def test_process_document_route_also_reports_not_configured(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    co, doc = ready

    response = client.post(f"{_invoices_url(co['id'])}/process-document/{doc['id']}")

    assert response.status_code == 200
    assert response.json()["configured"] is False


def test_process_document_is_company_scoped(client: TestClient) -> None:
    a = _company(client, "Company A")
    b = _company(client, "Company B")
    b_doc = _document(client, b["id"])

    response = client.post(f"{_invoices_url(a['id'])}/process-document/{b_doc['id']}")

    assert response.status_code == 404


# --- Model invariants ------------------------------------------------------


def test_invoice_starts_as_a_draft_and_is_not_processed(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient, db: Session
) -> None:
    """Having a row must never imply processing completed."""
    co, doc = ready
    created = _create(client, co["id"], doc["id"]).json()

    row = get_invoice(db, uuid.UUID(co["id"]), uuid.UUID(created["id"]))
    assert row.status is InvoiceStatus.DRAFT


def test_document_type_is_not_silently_changed_to_invoice(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    """Creating an invoice is not proof the document *is* an invoice."""
    co, doc = ready
    _create(client, co["id"], doc["id"])

    document = client.get(f"{COMPANIES_URL}/{co['id']}/documents/{doc['id']}").json()
    assert document["document_type"] == "UNKNOWN"


def test_multiple_invoices_may_reference_one_document(
    ready: tuple[dict[str, Any], dict[str, Any]], client: TestClient
) -> None:
    """No UNIQUE(document_id): a statement page may hold several invoices."""
    co, doc = ready

    first = _create(client, co["id"], doc["id"], invoice_number="INV-1")
    second = _create(client, co["id"], doc["id"], invoice_number="INV-2")

    assert first.status_code == 201
    assert second.status_code == 201
    assert len(client.get(_invoices_url(co["id"])).json()) == 2


def test_unlinking_a_vendor_keeps_the_invoice(
    client: TestClient, db: Session
) -> None:
    """SET NULL: removing a vendor master must not destroy invoice history."""
    co = _company(client)
    doc = _document(client, co["id"])
    vendor = _vendor(client, co["id"])
    created = _create(client, co["id"], doc["id"], vendor_id=vendor["id"]).json()

    row = db.get(Vendor, uuid.UUID(vendor["id"]))
    assert row is not None
    db.delete(row)
    db.commit()

    surviving = client.get(f"{_invoices_url(co['id'])}/{created['id']}")
    assert surviving.status_code == 200
    assert surviving.json()["vendor_id"] is None
