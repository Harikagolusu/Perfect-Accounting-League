"""Step 9: deterministic vendor mapping and historical grouping.

No test calls the real AI provider. A fake `InvoiceExtractor` is injected at
the one seam the invoice service uses, so the mapping behaviour is exercised
through the real API, invoice lifecycle and database.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.invoice import Invoice, InvoiceStatus, VendorMappingStatus
from app.services.invoice_extraction import ExtractionOutcome, ExtractionResult
from app.services.vendor_matching import (
    VendorMatchMethod,
    VendorMatchStatus,
    match_vendor,
    normalize_gstin,
    normalize_pan,
    normalize_vendor_name,
)

COMPANIES_URL = "/api/v1/companies"
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"

GSTIN_A = "27AABCT1234F1Z5"
PAN_A = "AABCT1234F"
GSTIN_B = "29AABCT1234F1Z5"
PAN_B = "AABCT1235G"


def _company(client: TestClient, name: str = "Mapping Co") -> dict[str, Any]:
    response = client.post(COMPANIES_URL, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _vendor(
    client: TestClient,
    company_id: str,
    *,
    name: str,
    gstin: str | None = None,
    pan: str | None = None,
    parent_group: str | None = None,
    default_expense_ledger: str | None = None,
    creditor_ledger: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "gstin": gstin,
        "pan": pan,
        "parent_group": parent_group,
        "default_expense_ledger": default_expense_ledger,
        "creditor_ledger": creditor_ledger,
    }
    if status is not None:
        payload["status"] = status
    response = client.post(
        f"{COMPANIES_URL}/{company_id}/vendors",
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _document(client: TestClient, company_id: str, name: str = "invoice.pdf") -> dict[str, Any]:
    response = client.post(
        f"{COMPANIES_URL}/{company_id}/documents",
        files={"file": (name, PDF_BYTES, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _invoices_url(company_id: str) -> str:
    return f"{COMPANIES_URL}/{company_id}/invoices"


def _process_document(client: TestClient, company_id: str, document_id: str) -> Any:
    return client.post(f"{_invoices_url(company_id)}/process-document/{document_id}")


def _get_invoice(client: TestClient, company_id: str, invoice_id: str) -> dict[str, Any]:
    response = client.get(f"{_invoices_url(company_id)}/{invoice_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _confirm(
    client: TestClient, company_id: str, invoice_id: str, vendor_id: str | None
) -> Any:
    return client.post(
        f"{_invoices_url(company_id)}/{invoice_id}/vendor-mapping",
        json={"vendor_id": vendor_id},
    )


def _values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "invoice_number": "MAP-001",
        "vendor_name": "ABC Traders Pvt Ltd",
        "vendor_gstin": None,
        "vendor_pan": None,
    }
    values.update(overrides)
    return values


class FakeExtractor:
    """Returns pre-built values without reading the document."""

    name = "fake"

    def __init__(
        self,
        values: dict[str, Any],
        *,
        outcome: ExtractionOutcome = ExtractionOutcome.PROCESSED,
        is_invoice: bool = True,
        issues: tuple[str, ...] = (),
    ) -> None:
        self.values = values
        self.outcome = outcome
        self.is_invoice = is_invoice
        self.issues = issues
        self.calls = 0

    def is_configured(self) -> bool:
        return True

    def extract(self, document: Any, raw_bytes: bytes) -> ExtractionResult:
        self.calls += 1
        return ExtractionResult(
            configured=True,
            outcome=self.outcome,
            reason=None if self.outcome is ExtractionOutcome.PROCESSED else "TEST_REVIEW",
            message="Test extraction.",
            values=self.values,
            issues=self.issues,
            is_invoice=self.is_invoice,
            provider="fake",
            model="fake-1",
        )


@pytest.fixture
def extractor(monkeypatch: pytest.MonkeyPatch) -> FakeExtractor:
    fake = FakeExtractor(_values())
    monkeypatch.setattr("app.services.invoice.get_extractor", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("ABC TRADERS", "ABC Traders"),
        ("ABC  Traders", "ABC Traders"),
        ("ABC-Traders", "ABC Traders"),
        ("ABC Traders Pvt. Ltd.", "ABC Traders Pvt Ltd"),
        ("ABC Traders Pvt Ltd", "ABC Traders Private Limited"),
        ("ABC Traders LLP", "ABC Traders"),
        ("ABC Traders & Co.", "ABC Traders and Company"),
    ],
)
def test_equivalent_vendor_names_normalize_together(left: str, right: str) -> None:
    assert normalize_vendor_name(left) == normalize_vendor_name(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("ABC Traders", "ABC Transport"),
        ("ABC Traders", "ABC Traders India"),
        ("ABC Traders", "ABC Traders Enterprises"),
    ],
)
def test_meaningful_words_are_not_removed(left: str, right: str) -> None:
    assert normalize_vendor_name(left) != normalize_vendor_name(right)


def test_normalization_does_not_empty_a_legal_form_only_name() -> None:
    assert normalize_vendor_name("Pvt Ltd") != ""
    assert normalize_vendor_name("Limited") == "limited"


def test_normalization_handles_blank_values() -> None:
    assert normalize_vendor_name(None) == ""
    assert normalize_vendor_name("   ") == ""


def test_gstin_and_pan_normalization_is_validating_and_uppercasing() -> None:
    assert normalize_gstin(f"  {GSTIN_A.lower()} ") == GSTIN_A
    assert normalize_pan(f"  {PAN_A.lower()} ") == PAN_A
    assert normalize_gstin("INVALIDGSTIN") is None
    assert normalize_pan("ABC123") is None
    assert normalize_gstin("   ") is None
    assert normalize_pan(None) is None


# ---------------------------------------------------------------------------
# Matcher service
# ---------------------------------------------------------------------------


def test_exact_gstin_match_wins_even_when_names_differ(client: TestClient, db: Session) -> None:
    company = _company(client)
    vendor = _vendor(client, company["id"], name="Different Trade Name", gstin=GSTIN_A, pan=PAN_A)

    result = match_vendor(
        db, uuid.UUID(company["id"]), "ABC Traders Pvt Ltd", GSTIN_A, None
    )

    assert result.status is VendorMatchStatus.MATCHED
    assert result.method is VendorMatchMethod.EXACT_GSTIN
    assert result.vendor is not None
    assert result.vendor.id == uuid.UUID(vendor["id"])
    assert result.requires_confirmation is False


def test_exact_pan_match_when_gstin_is_absent(client: TestClient, db: Session) -> None:
    company = _company(client)
    vendor = _vendor(client, company["id"], name="Different Trade Name", pan=PAN_A)

    result = match_vendor(db, uuid.UUID(company["id"]), "ABC Traders", None, PAN_A)

    assert result.status is VendorMatchStatus.MATCHED
    assert result.method is VendorMatchMethod.EXACT_PAN
    assert result.vendor is not None
    assert result.vendor.id == uuid.UUID(vendor["id"])


def test_unique_normalized_name_is_a_suggestion(client: TestClient, db: Session) -> None:
    company = _company(client)
    vendor = _vendor(client, company["id"], name="ABC Traders")

    result = match_vendor(
        db, uuid.UUID(company["id"]), "ABC Traders Pvt. Ltd.", None, None
    )

    assert result.status is VendorMatchStatus.MATCHED
    assert result.method is VendorMatchMethod.NORMALIZED_NAME
    assert result.requires_confirmation is True
    assert result.vendor is not None
    assert result.vendor.id == uuid.UUID(vendor["id"])


def test_no_match_when_identity_is_unknown(client: TestClient, db: Session) -> None:
    company = _company(client)
    _vendor(client, company["id"], name="Different Vendor")

    result = match_vendor(db, uuid.UUID(company["id"]), "ABC Traders", None, None)

    assert result.status is VendorMatchStatus.NO_MATCH
    assert result.vendor is None


def test_empty_vendor_information_has_a_specific_reason(client: TestClient, db: Session) -> None:
    company = _company(client)

    result = match_vendor(db, uuid.UUID(company["id"]), None, None, None)

    assert result.status is VendorMatchStatus.NO_MATCH
    assert result.reason == "NO_VENDOR_INFORMATION"


def test_invalid_identifiers_do_not_match_anything(client: TestClient, db: Session) -> None:
    company = _company(client)
    _vendor(client, company["id"], name="ABC Traders", gstin=GSTIN_A, pan=PAN_A)

    result = match_vendor(db, uuid.UUID(company["id"]), None, "INVALIDGSTIN", None)

    assert result.status is VendorMatchStatus.NO_MATCH
    assert result.reason == "INVALID_IDENTIFIERS"


def test_multiple_vendors_with_the_same_gstin_are_ambiguous(
    client: TestClient, db: Session
) -> None:
    company = _company(client)
    first = _vendor(client, company["id"], name="First Vendor", gstin=GSTIN_A)
    second = _vendor(client, company["id"], name="Second Vendor", gstin=GSTIN_A)

    result = match_vendor(db, uuid.UUID(company["id"]), None, GSTIN_A, None)

    assert result.status is VendorMatchStatus.AMBIGUOUS
    assert result.reason == "MULTIPLE_GSTIN_MATCHES"
    assert {candidate.id for candidate in result.candidates} == {
        uuid.UUID(first["id"]),
        uuid.UUID(second["id"]),
    }


def test_multiple_vendors_with_the_same_pan_are_ambiguous(
    client: TestClient, db: Session
) -> None:
    company = _company(client)
    first = _vendor(client, company["id"], name="First Vendor", pan=PAN_A)
    second = _vendor(client, company["id"], name="Second Vendor", pan=PAN_A)

    result = match_vendor(db, uuid.UUID(company["id"]), None, None, PAN_A)

    assert result.status is VendorMatchStatus.AMBIGUOUS
    assert result.reason == "MULTIPLE_PAN_MATCHES"
    assert {candidate.id for candidate in result.candidates} == {
        uuid.UUID(first["id"]),
        uuid.UUID(second["id"]),
    }


def test_valid_pan_still_matches_when_gstin_is_invalid(
    client: TestClient, db: Session
) -> None:
    company = _company(client)
    vendor = _vendor(client, company["id"], name="Different Trade Name", pan=PAN_A)

    result = match_vendor(
        db, uuid.UUID(company["id"]), None, "INVALIDGSTIN", PAN_A
    )

    assert result.status is VendorMatchStatus.MATCHED
    assert result.method is VendorMatchMethod.EXACT_PAN
    assert result.vendor is not None
    assert result.vendor.id == uuid.UUID(vendor["id"])


def test_multiple_normalized_names_are_ambiguous(client: TestClient, db: Session) -> None:
    company = _company(client)
    first = _vendor(client, company["id"], name="ABC Traders")
    second = _vendor(client, company["id"], name="ABC Traders Pvt Ltd")

    result = match_vendor(db, uuid.UUID(company["id"]), "ABC Traders Pvt Ltd", None, None)

    assert result.status is VendorMatchStatus.AMBIGUOUS
    assert result.reason == "MULTIPLE_NAME_MATCHES"
    assert {candidate.id for candidate in result.candidates} == {
        uuid.UUID(first["id"]),
        uuid.UUID(second["id"]),
    }


def test_gstin_and_name_conflict_is_ambiguous(client: TestClient, db: Session) -> None:
    company = _company(client)
    gstin_vendor = _vendor(client, company["id"], name="GSTIN Vendor", gstin=GSTIN_A)
    name_vendor = _vendor(client, company["id"], name="ABC Traders")

    result = match_vendor(
        db, uuid.UUID(company["id"]), "ABC Traders", GSTIN_A, None
    )

    assert result.status is VendorMatchStatus.AMBIGUOUS
    assert result.reason == "IDENTIFIER_NAME_CONFLICT"
    assert {candidate.id for candidate in result.candidates} == {
        uuid.UUID(gstin_vendor["id"]),
        uuid.UUID(name_vendor["id"]),
    }


def test_pan_and_name_conflict_is_ambiguous(client: TestClient, db: Session) -> None:
    company = _company(client)
    pan_vendor = _vendor(client, company["id"], name="PAN Vendor", pan=PAN_A)
    name_vendor = _vendor(client, company["id"], name="ABC Traders")

    result = match_vendor(db, uuid.UUID(company["id"]), "ABC Traders", None, PAN_A)

    assert result.status is VendorMatchStatus.AMBIGUOUS
    assert result.reason == "IDENTIFIER_NAME_CONFLICT"
    assert {candidate.id for candidate in result.candidates} == {
        uuid.UUID(pan_vendor["id"]),
        uuid.UUID(name_vendor["id"]),
    }


def test_gstin_and_pan_pointing_at_different_vendors_is_ambiguous(
    client: TestClient, db: Session
) -> None:
    company = _company(client)
    gstin_vendor = _vendor(client, company["id"], name="GSTIN Vendor", gstin=GSTIN_A)
    pan_vendor = _vendor(client, company["id"], name="PAN Vendor", pan=PAN_A)

    result = match_vendor(db, uuid.UUID(company["id"]), None, GSTIN_A, PAN_A)

    assert result.status is VendorMatchStatus.AMBIGUOUS
    assert result.reason == "IDENTIFIER_CONFLICT"
    assert {candidate.id for candidate in result.candidates} == {
        uuid.UUID(gstin_vendor["id"]),
        uuid.UUID(pan_vendor["id"]),
    }


def test_matching_never_leaves_the_company(client: TestClient, db: Session) -> None:
    company_a = _company(client, "Company A")
    company_b = _company(client, "Company B")
    _vendor(client, company_b["id"], name="ABC Traders", gstin=GSTIN_B, pan=PAN_B)

    result = match_vendor(
        db, uuid.UUID(company_a["id"]), "ABC Traders", GSTIN_B, PAN_B
    )

    assert result.status is VendorMatchStatus.NO_MATCH
    assert result.vendor is None


def test_inactive_vendors_do_not_receive_new_matches(client: TestClient, db: Session) -> None:
    company = _company(client)
    vendor = _vendor(client, company["id"], name="ABC Traders", gstin=GSTIN_A)
    deactivated = client.delete(f"{COMPANIES_URL}/{company['id']}/vendors/{vendor['id']}")
    assert deactivated.status_code == 200

    result = match_vendor(db, uuid.UUID(company["id"]), None, GSTIN_A, None)

    assert result.status is VendorMatchStatus.NO_MATCH


# ---------------------------------------------------------------------------
# Invoice integration
# ---------------------------------------------------------------------------


def test_gstin_match_assigns_vendor_and_marks_invoice_processed(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    vendor = _vendor(
        client,
        company["id"],
        name="ABC Traders",
        gstin=GSTIN_A,
        pan=PAN_A,
        parent_group="Raw Material",
        default_expense_ledger="Purchase - Raw Material",
        creditor_ledger="ABC Traders",
    )
    document = _document(client, company["id"])
    extractor.values = _values(vendor_gstin=GSTIN_A)

    response = _process_document(client, company["id"], document["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "PROCESSED"
    invoice = _get_invoice(client, company["id"], body["invoice_id"])
    assert invoice["vendor_id"] == vendor["id"]
    assert invoice["suggested_vendor_id"] is None
    assert invoice["vendor_mapping_status"] == "MATCHED"
    assert invoice["vendor_match_method"] == "EXACT_GSTIN"
    assert invoice["status"] == "PROCESSED"


def test_pan_match_assigns_vendor(client: TestClient, extractor: FakeExtractor) -> None:
    company = _company(client)
    vendor = _vendor(client, company["id"], name="ABC Traders", pan=PAN_A)
    document = _document(client, company["id"])
    extractor.values = _values(vendor_pan=PAN_A)

    body = _process_document(client, company["id"], document["id"]).json()
    invoice = _get_invoice(client, company["id"], body["invoice_id"])

    assert invoice["vendor_id"] == vendor["id"]
    assert invoice["vendor_mapping_status"] == "MATCHED"
    assert invoice["vendor_match_method"] == "EXACT_PAN"
    assert invoice["status"] == "PROCESSED"


def test_name_match_is_only_a_suggestion(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    vendor = _vendor(
        client,
        company["id"],
        name="ABC Traders",
        parent_group="Raw Material",
        default_expense_ledger="Purchase - Raw Material",
    )
    document = _document(client, company["id"])

    body = _process_document(client, company["id"], document["id"]).json()
    invoice = _get_invoice(client, company["id"], body["invoice_id"])

    assert invoice["vendor_id"] is None
    assert invoice["suggested_vendor_id"] == vendor["id"]
    assert invoice["vendor_mapping_status"] == "SUGGESTED"
    assert invoice["vendor_match_method"] == "NORMALIZED_NAME"
    # A pending suggestion is not an approved accounting assignment.
    assert invoice["status"] == "NEEDS_REVIEW"


def test_invoice_without_supplier_details_stays_unmapped(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    document = _document(client, company["id"])
    extractor.values = _values(vendor_name=None)

    body = _process_document(client, company["id"], document["id"]).json()
    invoice = _get_invoice(client, company["id"], body["invoice_id"])

    assert client.get(f"{COMPANIES_URL}/{company['id']}/vendors").json() == []
    assert invoice["vendor_id"] is None
    assert invoice["suggested_vendor_id"] is None
    assert invoice["vendor_mapping_status"] == "UNMAPPED"
    assert invoice["vendor_match_method"] is None
    assert invoice["status"] == "PROCESSED"


def test_ambiguous_match_forces_review(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    _vendor(client, company["id"], name="ABC Traders")
    _vendor(client, company["id"], name="ABC Traders Pvt Ltd")
    document = _document(client, company["id"])

    body = _process_document(client, company["id"], document["id"]).json()
    invoice = _get_invoice(client, company["id"], body["invoice_id"])

    assert invoice["vendor_id"] is None
    assert invoice["suggested_vendor_id"] is None
    assert invoice["vendor_mapping_status"] == "AMBIGUOUS"
    assert invoice["status"] == "NEEDS_REVIEW"


def test_conflicting_identifiers_force_review(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    _vendor(client, company["id"], name="GSTIN Vendor", gstin=GSTIN_A)
    _vendor(client, company["id"], name="ABC Traders")
    document = _document(client, company["id"])
    extractor.values = _values(vendor_gstin=GSTIN_A, vendor_name="ABC Traders")

    body = _process_document(client, company["id"], document["id"]).json()
    invoice = _get_invoice(client, company["id"], body["invoice_id"])

    assert invoice["vendor_id"] is None
    assert invoice["vendor_mapping_status"] == "AMBIGUOUS"
    assert invoice["status"] == "NEEDS_REVIEW"


def test_existing_manual_vendor_is_preserved(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    manual = _vendor(client, company["id"], name="Manual Vendor")
    other = _vendor(client, company["id"], name="Other Vendor", gstin=GSTIN_A)
    document = _document(client, company["id"])
    created = client.post(
        f"{_invoices_url(company['id'])}",
        json={
            "document_id": document["id"],
            "vendor_id": manual["id"],
            "vendor_name": "Manual Vendor",
        },
    )
    assert created.status_code == 201, created.text
    invoice_id = created.json()["id"]
    extractor.values = _values(vendor_gstin=GSTIN_A, vendor_name="Other Vendor")

    response = client.post(f"{_invoices_url(company['id'])}/{invoice_id}/process")

    assert response.status_code == 200, response.text
    invoice = _get_invoice(client, company["id"], invoice_id)
    assert invoice["vendor_id"] == manual["id"]
    assert invoice["vendor_id"] != other["id"]
    assert invoice["vendor_mapping_status"] == "CONFIRMED"


def test_unmatched_vendor_is_created_and_suggested(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    document = _document(client, company["id"])
    extractor.values = _values(vendor_gstin=GSTIN_A, vendor_pan=PAN_A)

    body = _process_document(client, company["id"], document["id"]).json()

    listed = client.get(f"{COMPANIES_URL}/{company['id']}/vendors")
    assert listed.status_code == 200
    vendors = listed.json()
    assert len(vendors) == 1
    vendor = vendors[0]
    assert vendor["name"] == "ABC Traders Pvt Ltd"
    assert vendor["gstin"] == GSTIN_A
    assert vendor["pan"] == PAN_A
    assert vendor["creditor_ledger"] == "ABC Traders Pvt Ltd"
    assert vendor["parent_group"] == "Sundry Creditors"
    assert vendor["default_expense_ledger"] is None
    assert vendor["status"] == "NEEDS_MAPPING"

    invoice = _get_invoice(client, company["id"], body["invoice_id"])
    assert invoice["vendor_id"] is None
    assert invoice["suggested_vendor_id"] == vendor["id"]
    assert invoice["vendor_mapping_status"] == "SUGGESTED"
    assert invoice["vendor_match_method"] is None
    assert invoice["status"] == "NEEDS_REVIEW"
    assert body["status"] == "NEEDS_REVIEW"


def test_confirming_a_suggestion_assigns_the_vendor(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    vendor = _vendor(client, company["id"], name="ABC Traders")
    document = _document(client, company["id"])
    body = _process_document(client, company["id"], document["id"]).json()

    response = _confirm(client, company["id"], body["invoice_id"], vendor["id"])

    assert response.status_code == 200, response.text
    invoice = response.json()
    assert invoice["vendor_id"] == vendor["id"]
    assert invoice["suggested_vendor_id"] is None
    assert invoice["vendor_mapping_status"] == "CONFIRMED"
    # Confirming while the invoice is already in review does not change status.
    assert invoice["status"] == "NEEDS_REVIEW"
    # The evidence that produced the suggestion is kept.
    assert invoice["vendor_match_method"] == "NORMALIZED_NAME"


def test_approving_data_with_pending_mapping_is_rejected(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    _vendor(client, company["id"], name="ABC Traders")
    document = _document(client, company["id"])
    body = _process_document(client, company["id"], document["id"]).json()
    assert body["status"] == "NEEDS_REVIEW"

    response = client.patch(
        f"{_invoices_url(company['id'])}/{body['invoice_id']}",
        json={"status": "PROCESSED"},
    )

    assert response.status_code == 409
    assert "vendor suggestion" in response.json()["detail"].lower()
    invoice = _get_invoice(client, company["id"], body["invoice_id"])
    assert invoice["status"] == "NEEDS_REVIEW"
    assert invoice["vendor_mapping_status"] == "SUGGESTED"


def test_confirming_a_mapping_reopens_a_processed_invoice(
    client: TestClient, extractor: FakeExtractor, db: Session
) -> None:
    company = _company(client)
    vendor = _vendor(client, company["id"], name="ABC Traders")
    document = _document(client, company["id"])
    body = _process_document(client, company["id"], document["id"]).json()
    invoice = db.get(Invoice, uuid.UUID(body["invoice_id"]))
    assert invoice is not None
    # Reproduce the state an approval that skipped the mapping review left.
    invoice.status = InvoiceStatus.PROCESSED
    db.commit()

    confirmed = _confirm(client, company["id"], body["invoice_id"], vendor["id"])

    assert confirmed.status_code == 200, confirmed.text
    reopened = confirmed.json()
    assert reopened["status"] == "NEEDS_REVIEW"
    assert reopened["vendor_mapping_status"] == "CONFIRMED"

    approved = client.patch(
        f"{_invoices_url(company['id'])}/{body['invoice_id']}",
        json={"status": "PROCESSED"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "PROCESSED"


def test_rejecting_a_suggestion_is_not_reapplied_on_reprocess(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    _vendor(client, company["id"], name="ABC Traders")
    document = _document(client, company["id"])
    body = _process_document(client, company["id"], document["id"]).json()
    invoice_id = body["invoice_id"]

    rejected = _confirm(client, company["id"], invoice_id, None)
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["vendor_mapping_status"] == "REJECTED"
    assert rejected.json()["suggested_vendor_id"] is None
    assert rejected.json()["vendor_id"] is None

    again = _process_document(client, company["id"], document["id"])

    assert again.status_code == 200, again.text
    invoice = _get_invoice(client, company["id"], invoice_id)
    assert invoice["vendor_mapping_status"] == "REJECTED"
    assert invoice["vendor_id"] is None
    assert invoice["suggested_vendor_id"] is None


def test_rejected_mapping_can_be_replaced_by_an_explicit_assignment(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    vendor = _vendor(client, company["id"], name="ABC Traders")
    document = _document(client, company["id"])
    body = _process_document(client, company["id"], document["id"]).json()
    _confirm(client, company["id"], body["invoice_id"], None)

    response = _confirm(client, company["id"], body["invoice_id"], vendor["id"])

    assert response.status_code == 200, response.text
    invoice = response.json()
    assert invoice["vendor_id"] == vendor["id"]
    assert invoice["vendor_mapping_status"] == "CONFIRMED"


def test_changing_a_confirmed_mapping_keeps_the_previous_vendor(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    first = _vendor(client, company["id"], name="ABC Traders")
    second = _vendor(client, company["id"], name="Other Vendor")
    document = _document(client, company["id"])
    body = _process_document(client, company["id"], document["id"]).json()
    _confirm(client, company["id"], body["invoice_id"], first["id"])

    response = _confirm(client, company["id"], body["invoice_id"], second["id"])

    assert response.status_code == 200, response.text
    invoice = response.json()
    assert invoice["vendor_id"] == second["id"]
    assert invoice["previous_vendor_id"] == first["id"]
    assert invoice["vendor_mapping_status"] == "CONFIRMED"


def test_unlinking_a_confirmed_mapping_keeps_the_previous_vendor(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    vendor = _vendor(client, company["id"], name="ABC Traders")
    document = _document(client, company["id"])
    created = client.post(
        f"{_invoices_url(company['id'])}",
        json={"document_id": document["id"], "vendor_id": vendor["id"]},
    ).json()

    response = client.patch(
        f"{_invoices_url(company['id'])}/{created['id']}",
        json={"vendor_id": None},
    )

    assert response.status_code == 200, response.text
    invoice = response.json()
    assert invoice["vendor_id"] is None
    assert invoice["previous_vendor_id"] == vendor["id"]
    assert invoice["vendor_mapping_status"] == "UNMAPPED"


def test_rejecting_an_assigned_vendor_does_not_unassign_it(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    vendor = _vendor(client, company["id"], name="ABC Traders", gstin=GSTIN_A)
    document = _document(client, company["id"])
    extractor.values = _values(vendor_gstin=GSTIN_A)
    body = _process_document(client, company["id"], document["id"]).json()

    response = _confirm(client, company["id"], body["invoice_id"], None)

    assert response.status_code == 409, response.text
    invoice = _get_invoice(client, company["id"], body["invoice_id"])
    assert invoice["vendor_id"] == vendor["id"]
    assert invoice["vendor_mapping_status"] == "MATCHED"


def test_cross_company_vendor_assignment_is_rejected(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company_a = _company(client, "Company A")
    company_b = _company(client, "Company B")
    b_vendor = _vendor(client, company_b["id"], name="ABC Traders")
    _vendor(client, company_a["id"], name="ABC Traders")
    document = _document(client, company_a["id"])
    body = _process_document(client, company_a["id"], document["id"]).json()

    response = _confirm(client, company_a["id"], body["invoice_id"], b_vendor["id"])

    assert response.status_code == 404, response.text
    invoice = _get_invoice(client, company_a["id"], body["invoice_id"])
    assert invoice["vendor_id"] is None
    assert invoice["vendor_mapping_status"] == "SUGGESTED"


def test_mapping_endpoint_cannot_reach_another_company_invoice(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company_a = _company(client, "Company A")
    company_b = _company(client, "Company B")
    document = _document(client, company_b["id"])
    body = _process_document(client, company_b["id"], document["id"]).json()

    response = _confirm(client, company_a["id"], body["invoice_id"], None)

    assert response.status_code == 404


def test_confirming_an_unknown_vendor_is_404(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    document = _document(client, company["id"])
    body = _process_document(client, company["id"], document["id"]).json()

    response = _confirm(client, company["id"], body["invoice_id"], str(uuid.uuid4()))

    assert response.status_code == 404


def test_vendor_mapping_body_requires_the_field(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    document = _document(client, company["id"])
    body = _process_document(client, company["id"], document["id"]).json()

    response = client.post(
        f"{_invoices_url(company['id'])}/{body['invoice_id']}/vendor-mapping", json={}
    )

    assert response.status_code == 422


def test_previously_confirmed_name_mapping_is_reused(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    vendor = _vendor(
        client,
        company["id"],
        name="ABC Traders",
        parent_group="Raw Material",
        default_expense_ledger="Purchase - Raw Material",
    )
    first_document = _document(client, company["id"], "first.pdf")
    first = _process_document(client, company["id"], first_document["id"]).json()
    assert _confirm(client, company["id"], first["invoice_id"], vendor["id"]).status_code == 200

    second_document = _document(client, company["id"], "second.pdf")
    second = _process_document(client, company["id"], second_document["id"]).json()

    assert second["status"] == "PROCESSED"
    invoice = _get_invoice(client, company["id"], second["invoice_id"])
    assert invoice["vendor_id"] == vendor["id"]
    assert invoice["suggested_vendor_id"] is None
    assert invoice["vendor_mapping_status"] == "MATCHED"
    assert invoice["vendor_match_method"] == "NORMALIZED_NAME"

    # The historical grouping is the Vendor Master's existing fields.
    stored_vendor = client.get(
        f"{COMPANIES_URL}/{company['id']}/vendors/{vendor['id']}"
    ).json()
    assert stored_vendor["parent_group"] == "Raw Material"
    assert stored_vendor["default_expense_ledger"] == "Purchase - Raw Material"


def test_historical_grouping_is_company_scoped(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company_a = _company(client, "Company A")
    company_b = _company(client, "Company B")
    vendor_a = _vendor(
        client,
        company_a["id"],
        name="ABC Traders",
        parent_group="Raw Material",
        default_expense_ledger="Purchase - Raw Material",
    )
    _vendor(
        client,
        company_b["id"],
        name="ABC Traders",
        parent_group="Office Supplies",
        default_expense_ledger="Office Expenses",
    )
    document = _document(client, company_a["id"])
    body = _process_document(client, company_a["id"], document["id"]).json()

    invoice = _get_invoice(client, company_a["id"], body["invoice_id"])
    assert invoice["suggested_vendor_id"] == vendor_a["id"]

    grouped = client.get(
        f"{COMPANIES_URL}/{company_a['id']}/vendors/{invoice['suggested_vendor_id']}"
    ).json()
    assert grouped["parent_group"] == "Raw Material"
    assert grouped["default_expense_ledger"] == "Purchase - Raw Material"


def test_cross_company_identity_does_not_leak_into_matching(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company_a = _company(client, "Company A")
    company_b = _company(client, "Company B")
    vendor_b = _vendor(client, company_b["id"], name="ABC Traders", gstin=GSTIN_B)
    document = _document(client, company_a["id"])
    extractor.values = _values(vendor_gstin=GSTIN_B)

    body = _process_document(client, company_a["id"], document["id"]).json()

    vendor_a = client.get(f"{COMPANIES_URL}/{company_a['id']}/vendors").json()[0]
    assert vendor_a["id"] != vendor_b["id"]
    assert vendor_a["gstin"] == GSTIN_B

    invoice = _get_invoice(client, company_a["id"], body["invoice_id"])
    assert invoice["vendor_id"] is None
    assert invoice["suggested_vendor_id"] == vendor_a["id"]
    assert invoice["vendor_mapping_status"] == "SUGGESTED"


def test_invalid_gstin_is_preserved_and_reported(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    _vendor(client, company["id"], name="ABC Traders")
    document = _document(client, company["id"])
    extractor.outcome = ExtractionOutcome.NEEDS_REVIEW
    extractor.issues = ("Vendor GSTIN does not match the expected GSTIN format.",)
    extractor.values = _values(vendor_gstin="INVALIDGSTIN")

    body = _process_document(client, company["id"], document["id"]).json()

    assert body["status"] == "NEEDS_REVIEW"
    invoice = _get_invoice(client, company["id"], body["invoice_id"])
    # The printed value is never silently changed or dropped.
    assert invoice["vendor_gstin"] == "INVALIDGSTIN"
    # The invalid identifier was not used as evidence; the name suggestion is.
    assert invoice["suggested_vendor_id"] is not None
    assert invoice["vendor_mapping_status"] == "SUGGESTED"


def test_invalid_pan_is_preserved_and_reported(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    _vendor(client, company["id"], name="ABC Traders")
    document = _document(client, company["id"])
    extractor.outcome = ExtractionOutcome.NEEDS_REVIEW
    extractor.issues = ("Vendor PAN does not match the expected PAN format.",)
    extractor.values = _values(vendor_pan="BADPAN")

    body = _process_document(client, company["id"], document["id"]).json()

    invoice = _get_invoice(client, company["id"], body["invoice_id"])
    assert invoice["vendor_pan"] == "BADPAN"
    assert invoice["vendor_mapping_status"] == "SUGGESTED"


def test_patching_vendor_id_confirms_the_mapping(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    vendor = _vendor(client, company["id"], name="Manual Vendor")
    document = _document(client, company["id"])
    created = client.post(
        f"{_invoices_url(company['id'])}",
        json={"document_id": document["id"]},
    ).json()

    response = client.patch(
        f"{_invoices_url(company['id'])}/{created['id']}",
        json={"vendor_id": vendor["id"]},
    )

    assert response.status_code == 200, response.text
    invoice = response.json()
    assert invoice["vendor_id"] == vendor["id"]
    assert invoice["vendor_mapping_status"] == "CONFIRMED"
    assert invoice["vendor_match_method"] is None


def test_editing_the_printed_identity_invalidates_a_suggestion(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    _vendor(client, company["id"], name="ABC Traders")
    document = _document(client, company["id"])
    body = _process_document(client, company["id"], document["id"]).json()
    assert body["invoice_id"]

    response = client.patch(
        f"{_invoices_url(company['id'])}/{body['invoice_id']}",
        json={"vendor_name": "Different Trading Co"},
    )

    assert response.status_code == 200, response.text
    invoice = response.json()
    assert invoice["vendor_name"] == "Different Trading Co"
    assert invoice["suggested_vendor_id"] is None
    assert invoice["vendor_mapping_status"] == "UNMAPPED"
    assert invoice["vendor_match_method"] is None


def test_editing_the_identity_of_a_confirmed_mapping_keeps_the_vendor(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    vendor = _vendor(client, company["id"], name="ABC Traders")
    document = _document(client, company["id"])
    body = _process_document(client, company["id"], document["id"]).json()
    _confirm(client, company["id"], body["invoice_id"], vendor["id"])

    response = client.patch(
        f"{_invoices_url(company['id'])}/{body['invoice_id']}",
        json={"vendor_name": "Corrected Printed Name"},
    )

    assert response.status_code == 200, response.text
    invoice = response.json()
    assert invoice["vendor_id"] == vendor["id"]
    assert invoice["vendor_mapping_status"] == "CONFIRMED"


def test_match_preview_returns_the_ambiguous_candidates(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    first = _vendor(client, company["id"], name="ABC Traders")
    second = _vendor(client, company["id"], name="ABC Traders Pvt Ltd")
    document = _document(client, company["id"])
    body = _process_document(client, company["id"], document["id"]).json()

    response = client.get(
        f"{_invoices_url(company['id'])}/{body['invoice_id']}/vendor-match"
    )

    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["status"] == "AMBIGUOUS"
    assert preview["reason"] == "MULTIPLE_NAME_MATCHES"
    assert preview["vendor_id"] is None
    assert preview["requires_confirmation"] is False
    assert {candidate["id"] for candidate in preview["candidates"]} == {
        first["id"],
        second["id"],
    }
    # Reading the preview must not change the invoice.
    invoice = _get_invoice(client, company["id"], body["invoice_id"])
    assert invoice["vendor_id"] is None
    assert invoice["vendor_mapping_status"] == "AMBIGUOUS"


def test_match_preview_returns_a_matched_vendor(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    vendor = _vendor(client, company["id"], name="ABC Traders", gstin=GSTIN_A)
    document = _document(client, company["id"])
    extractor.values = _values(vendor_gstin=GSTIN_A)
    body = _process_document(client, company["id"], document["id"]).json()

    preview = client.get(
        f"{_invoices_url(company['id'])}/{body['invoice_id']}/vendor-match"
    ).json()

    assert preview["status"] == "MATCHED"
    assert preview["method"] == "EXACT_GSTIN"
    assert preview["vendor_id"] == vendor["id"]
    assert preview["requires_confirmation"] is False
    assert [candidate["id"] for candidate in preview["candidates"]] == [vendor["id"]]


def test_match_preview_marks_a_unique_name_match_as_a_suggestion(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    vendor = _vendor(client, company["id"], name="ABC Traders")
    document = _document(client, company["id"])
    extractor.values = _values(vendor_name="ABC Traders Pvt Ltd.")
    body = _process_document(client, company["id"], document["id"]).json()

    preview = client.get(
        f"{_invoices_url(company['id'])}/{body['invoice_id']}/vendor-match"
    ).json()

    assert preview["status"] == "MATCHED"
    assert preview["method"] == "NORMALIZED_NAME"
    assert preview["vendor_id"] == vendor["id"]
    assert preview["requires_confirmation"] is True
    assert [candidate["id"] for candidate in preview["candidates"]] == [vendor["id"]]


def test_match_preview_with_no_match_has_no_candidates(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company = _company(client)
    document = _document(client, company["id"])
    created = client.post(
        _invoices_url(company["id"]),
        json={"document_id": document["id"], "vendor_name": "ABC Traders"},
    ).json()

    preview = client.get(
        f"{_invoices_url(company['id'])}/{created['id']}/vendor-match"
    ).json()

    assert preview["status"] == "NO_MATCH"
    assert preview["vendor_id"] is None
    assert preview["candidates"] == []


def test_match_preview_is_company_scoped(
    client: TestClient, extractor: FakeExtractor
) -> None:
    company_a = _company(client, "Company A")
    company_b = _company(client, "Company B")
    document = _document(client, company_b["id"])
    body = _process_document(client, company_b["id"], document["id"]).json()

    response = client.get(
        f"{_invoices_url(company_a['id'])}/{body['invoice_id']}/vendor-match"
    )

    assert response.status_code == 404


def test_mapping_status_is_exposed_on_the_invoice_schema() -> None:
    """The API contract names every state, so the UI cannot invent one."""
    assert {status.value for status in VendorMappingStatus} == {
        "UNMAPPED",
        "SUGGESTED",
        "MATCHED",
        "CONFIRMED",
        "REJECTED",
        "AMBIGUOUS",
    }
