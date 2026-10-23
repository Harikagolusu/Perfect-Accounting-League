"""Tests for the Document Inbox.

Covers file validation, safe storage, company isolation and the delete
lifecycle. Storage is redirected to a temporary directory by the session
fixture in `conftest.py`, so these tests never touch real project files.
"""

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.storage import resolve_storage_path
from app.services.document import get_document

COMPANIES_URL = "/api/v1/companies"

# Minimal byte prefixes that satisfy the content-signature checks. They are not
# valid, renderable documents: Step 4 stores and validates bytes, it does not
# parse them.
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 16
XLSX_BYTES = b"PK\x03\x04\x14\x00\x06\x00" + b"\x00" * 16
XLS_BYTES = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 16
DOCX_BYTES = b"PK\x03\x04\x14\x00\x06\x00" + b"\x00" * 16


def _documents_url(company_id: Any) -> str:
    return f"{COMPANIES_URL}/{company_id}/documents"


def _company(client: TestClient, name: str = "Test Co") -> dict[str, Any]:
    response = client.post(COMPANIES_URL, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _upload(
    client: TestClient,
    company_id: Any,
    data: bytes,
    filename: str,
    content_type: str,
) -> Any:
    return client.post(
        _documents_url(company_id),
        files={"file": (filename, data, content_type)},
    )


def _stored_path(session: Session, company_id: str, document_id: str) -> str:
    """The internal storage path, which the API deliberately hides."""
    return get_document(session, uuid.UUID(company_id), uuid.UUID(document_id)).file_path


def _storage_root() -> Path:
    return get_settings().document_storage_dir


@pytest.fixture
def company(client: TestClient) -> dict[str, Any]:
    return _company(client)


@pytest.fixture
def two_companies(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    a = _company(client, "Company A")
    b = _company(client, "Company B")
    _upload(client, a["id"], PDF_BYTES, "invoice-a.pdf", "application/pdf")
    _upload(client, b["id"], PDF_BYTES, "invoice-b.pdf", "application/pdf")
    return a, b


# --- Accepted formats ------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "filename", "content_type", "expected_mime"),
    [
        (PDF_BYTES, "invoice_march.pdf", "application/pdf", "application/pdf"),
        (JPEG_BYTES, "scan.jpg", "image/jpeg", "image/jpeg"),
        (PNG_BYTES, "scan.png", "image/png", "image/png"),
        (
            XLSX_BYTES,
            "statement.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        (XLS_BYTES, "old.xls", "application/vnd.ms-excel", "application/vnd.ms-excel"),
        (
            DOCX_BYTES,
            "page.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    ],
)
def test_upload_supported_formats(
    client: TestClient,
    company: dict[str, Any],
    data: bytes,
    filename: str,
    content_type: str,
    expected_mime: str,
) -> None:
    response = _upload(client, company["id"], data, filename, content_type)

    assert response.status_code == 201, response.text
    assert response.json()["file_type"] == expected_mime


def test_jpeg_long_extension_is_accepted(
    client: TestClient, company: dict[str, Any]
) -> None:
    assert _upload(client, company["id"], JPEG_BYTES, "scan.jpeg", "image/jpeg").status_code == 201


# --- Rejections ------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "data", "content_type"),
    [
        ("notes.txt", b"hello", "text/plain"),
        ("payload.exe", b"MZ\x90\x00", "application/octet-stream"),
        ("archive.zip", b"PK\x03\x04\x14\x00", "application/zip"),
        ("shell.sh", b"#!/bin/sh\necho hi", "application/x-sh"),
    ],
)
def test_reject_unsupported_file_types(
    client: TestClient,
    company: dict[str, Any],
    filename: str,
    data: bytes,
    content_type: str,
) -> None:
    response = _upload(client, company["id"], data, filename, content_type)

    assert response.status_code == 415, response.text


def test_reject_content_that_does_not_match_the_extension(
    client: TestClient, company: dict[str, Any]
) -> None:
    """A PDF-named file full of PNG bytes is rejected."""
    response = _upload(client, company["id"], PNG_BYTES, "invoice.pdf", "application/pdf")

    assert response.status_code == 415
    assert "do not look like" in response.json()["detail"]


def test_reject_mime_that_contradicts_the_extension(
    client: TestClient, company: dict[str, Any]
) -> None:
    response = _upload(client, company["id"], PDF_BYTES, "invoice.pdf", "image/png")

    assert response.status_code == 415


def test_reject_renamed_jpeg_with_png_bytes(
    client: TestClient, company: dict[str, Any]
) -> None:
    """Same MIME family is not enough: the bytes must match the extension."""
    response = _upload(
        client, company["id"], PNG_BYTES, "invoice.jpg", "image/png"
    )

    assert response.status_code == 415


def test_reject_renamed_docx_with_pdf_bytes(
    client: TestClient, company: dict[str, Any]
) -> None:
    """A document-office and a PDF share the application/* family."""
    response = _upload(
        client, company["id"], PDF_BYTES, "invoice.docx", "application/pdf"
    )

    assert response.status_code == 415





def test_generic_mime_is_still_content_checked(
    client: TestClient, company: dict[str, Any]
) -> None:
    """Browsers send octet-stream often; the signature decides."""
    ok = _upload(
        client, company["id"], PDF_BYTES, "invoice.pdf", "application/octet-stream"
    )
    bad = _upload(
        client, company["id"], b"not a pdf", "other.pdf", "application/octet-stream"
    )

    assert ok.status_code == 201
    assert bad.status_code == 415


def test_reject_empty_file(client: TestClient, company: dict[str, Any]) -> None:
    response = _upload(client, company["id"], b"", "empty.pdf", "application/pdf")

    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_reject_oversized_file(
    client: TestClient, company: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The limit is configurable, so shrink it instead of building 20 MB."""
    monkeypatch.setattr(get_settings(), "document_max_upload_bytes", 16)

    response = _upload(
        client, company["id"], PDF_BYTES + b"0" * 64, "big.pdf", "application/pdf"
    )

    assert response.status_code == 413
    assert "maximum upload size" in response.json()["detail"]


def test_reject_blank_filename(client: TestClient, company: dict[str, Any]) -> None:
    response = client.post(
        _documents_url(company["id"]),
        files={"file": ("   ", PDF_BYTES, "application/pdf")},
    )

    assert response.status_code == 400


def test_rejected_upload_writes_nothing(
    client: TestClient, company: dict[str, Any]
) -> None:
    _upload(client, company["id"], b"not a pdf", "bad.pdf", "application/pdf")

    assert list(_storage_root().rglob("*")) == []


# --- Record shape ----------------------------------------------------------


def test_new_document_starts_uploaded_and_unknown(
    client: TestClient, company: dict[str, Any]
) -> None:
    body = _upload(
        client, company["id"], PDF_BYTES, "invoice_march.pdf", "application/pdf"
    ).json()

    assert body["status"] == "UPLOADED"
    assert body["document_type"] == "UNKNOWN"
    assert body["company_id"] == company["id"]
    assert body["file_size"] == len(PDF_BYTES)
    assert body["created_at"] and body["updated_at"]


def test_response_does_not_expose_internals(
    client: TestClient, company: dict[str, Any]
) -> None:
    body = _upload(client, company["id"], PDF_BYTES, "invoice.pdf", "application/pdf").json()

    assert "file_path" not in body
    assert "stored_filename" not in body


# --- Safe filenames and storage -------------------------------------------


def test_stored_filename_is_generated_not_taken_from_the_client(
    client: TestClient, company: dict[str, Any], db: Session
) -> None:
    body = _upload(
        client, company["id"], PDF_BYTES, "../../etc/passwd.pdf", "application/pdf"
    ).json()

    # The original name is preserved for display...
    assert body["original_filename"] == "../../etc/passwd.pdf"

    # ...but the name on disk is a generated UUID.
    stored = Path(_stored_path(db, company["id"], body["id"])).name
    assert "/" not in stored
    assert ".." not in stored
    assert stored.endswith(".pdf")
    assert uuid.UUID(stored.removesuffix(".pdf")).version == 4


def test_storage_stays_inside_the_root(
    client: TestClient, company: dict[str, Any]
) -> None:
    """A traversal-shaped name must not escape the storage directory."""
    _upload(client, company["id"], PDF_BYTES, "../../evil.pdf", "application/pdf")

    on_disk = [p for p in _storage_root().rglob("*") if p.is_file()]
    assert on_disk, "expected the document to be stored"
    for path in on_disk:
        assert _storage_root() in path.parents


def test_file_is_written_under_the_company_directory(
    client: TestClient, company: dict[str, Any], db: Session
) -> None:
    body = _upload(client, company["id"], PDF_BYTES, "invoice.pdf", "application/pdf").json()

    relative = _stored_path(db, company["id"], body["id"])
    assert relative.startswith(f"{company['id']}/")
    assert resolve_storage_path(relative).read_bytes() == PDF_BYTES


# --- Listing ---------------------------------------------------------------


def test_list_documents_for_company(
    client: TestClient, two_companies: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    a, _ = two_companies

    listed = client.get(_documents_url(a["id"]))

    assert listed.status_code == 200
    assert [d["original_filename"] for d in listed.json()] == ["invoice-a.pdf"]


def test_list_is_empty_for_a_company_with_none(
    client: TestClient, company: dict[str, Any]
) -> None:
    response = client.get(_documents_url(company["id"]))

    assert response.status_code == 200
    assert response.json() == []


def test_list_filters_by_status(client: TestClient, company: dict[str, Any]) -> None:
    _upload(client, company["id"], PDF_BYTES, "a.pdf", "application/pdf")

    matched = client.get(_documents_url(company["id"]), params={"status": "UPLOADED"})
    unmatched = client.get(_documents_url(company["id"]), params={"status": "FAILED"})

    assert len(matched.json()) == 1
    assert unmatched.json() == []


def test_list_filters_by_document_type(client: TestClient, company: dict[str, Any]) -> None:
    _upload(client, company["id"], PDF_BYTES, "a.pdf", "application/pdf")

    matched = client.get(
        _documents_url(company["id"]), params={"document_type": "UNKNOWN"}
    )
    unmatched = client.get(
        _documents_url(company["id"]), params={"document_type": "INVOICE"}
    )

    assert len(matched.json()) == 1
    assert unmatched.json() == []


def test_list_rejects_an_invalid_filter(client: TestClient, company: dict[str, Any]) -> None:
    response = client.get(_documents_url(company["id"]), params={"status": "NOPE"})

    assert response.status_code == 422


# --- Get -------------------------------------------------------------------


def test_get_document(client: TestClient, company: dict[str, Any]) -> None:
    created = _upload(client, company["id"], PDF_BYTES, "invoice.pdf", "application/pdf").json()

    response = client.get(f"{_documents_url(company['id'])}/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_get_nonexistent_document_returns_404(
    client: TestClient, company: dict[str, Any]
) -> None:
    response = client.get(f"{_documents_url(company['id'])}/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found"


def test_file_preview_returns_stored_bytes(
    client: TestClient, company: dict[str, Any]
) -> None:
    created = _upload(client, company["id"], PDF_BYTES, "invoice.pdf", "application/pdf").json()

    response = client.get(f"{_documents_url(company['id'])}/{created['id']}/file")

    assert response.status_code == 200
    assert response.content == PDF_BYTES
    assert response.headers["content-type"].startswith("application/pdf")
    assert "inline" in response.headers["content-disposition"]


def test_file_download_uses_attachment_disposition(
    client: TestClient, company: dict[str, Any]
) -> None:
    created = _upload(client, company["id"], PDF_BYTES, "invoice.pdf", "application/pdf").json()

    response = client.get(
        f"{_documents_url(company['id'])}/{created['id']}/file",
        params={"download": True},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert "attachment" in response.headers["content-disposition"]


def test_file_preview_returns_404_when_stored_file_is_missing(
    client: TestClient, company: dict[str, Any], db: Session
) -> None:
    created = _upload(client, company["id"], PDF_BYTES, "invoice.pdf", "application/pdf").json()
    resolve_storage_path(_stored_path(db, company["id"], created["id"])).unlink()

    response = client.get(f"{_documents_url(company['id'])}/{created['id']}/file")

    assert response.status_code == 404
    assert response.json()["detail"] == "Stored file not found"


# --- Delete ----------------------------------------------------------------


def test_delete_removes_row_and_file(
    client: TestClient, company: dict[str, Any], db: Session
) -> None:
    created = _upload(client, company["id"], PDF_BYTES, "invoice.pdf", "application/pdf").json()
    stored = resolve_storage_path(_stored_path(db, company["id"], created["id"]))
    assert stored.is_file()

    response = client.delete(f"{_documents_url(company['id'])}/{created['id']}")

    assert response.status_code == 204
    assert not stored.exists()
    assert client.get(f"{_documents_url(company['id'])}/{created['id']}").status_code == 404
    assert client.get(_documents_url(company["id"])).json() == []


def test_delete_tolerates_a_file_already_removed_outside_the_app(
    client: TestClient, company: dict[str, Any], db: Session
) -> None:
    """A missing file must not stop the database record being cleaned up."""
    created = _upload(client, company["id"], PDF_BYTES, "invoice.pdf", "application/pdf").json()
    resolve_storage_path(_stored_path(db, company["id"], created["id"])).unlink()

    response = client.delete(f"{_documents_url(company['id'])}/{created['id']}")

    assert response.status_code == 204
    assert client.get(_documents_url(company["id"])).json() == []


def test_delete_nonexistent_document_returns_404(
    client: TestClient, company: dict[str, Any]
) -> None:
    response = client.delete(f"{_documents_url(company['id'])}/{uuid.uuid4()}")

    assert response.status_code == 404


# --- Company isolation -----------------------------------------------------


def test_inboxes_are_isolated(
    client: TestClient, two_companies: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    a, b = two_companies

    listed_a = client.get(_documents_url(a["id"])).json()
    listed_b = client.get(_documents_url(b["id"])).json()

    assert [d["original_filename"] for d in listed_a] == ["invoice-a.pdf"]
    assert [d["original_filename"] for d in listed_b] == ["invoice-b.pdf"]


def test_cross_company_get_returns_404(
    client: TestClient, two_companies: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    a, b = two_companies
    b_document = client.get(_documents_url(b["id"])).json()[0]

    response = client.get(f"{_documents_url(a['id'])}/{b_document['id']}")

    assert response.status_code == 404, response.text


def test_cross_company_file_preview_returns_404(
    client: TestClient, two_companies: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    a, b = two_companies
    b_document = client.get(_documents_url(b["id"])).json()[0]

    response = client.get(f"{_documents_url(a['id'])}/{b_document['id']}/file")

    assert response.status_code == 404, response.text


def test_cross_company_delete_returns_404(
    client: TestClient, two_companies: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    a, b = two_companies
    b_document = client.get(_documents_url(b["id"])).json()[0]

    response = client.delete(f"{_documents_url(a['id'])}/{b_document['id']}")

    assert response.status_code == 404, response.text
    # B's document must survive the attempt untouched.
    assert client.get(f"{_documents_url(b['id'])}/{b_document['id']}").status_code == 200


# --- Company must exist ----------------------------------------------------


def test_upload_to_nonexistent_company_returns_404(client: TestClient) -> None:
    response = _upload(client, uuid.uuid4(), PDF_BYTES, "invoice.pdf", "application/pdf")

    assert response.status_code == 404
    assert response.json()["detail"] == "Company not found"


def test_get_under_nonexistent_company_returns_404(client: TestClient) -> None:
    response = client.get(f"{_documents_url(uuid.uuid4())}/{uuid.uuid4()}")

    assert response.status_code == 404


def test_upload_rejects_a_non_uuid_company(client: TestClient) -> None:
    response = _upload(client, "not-a-uuid", PDF_BYTES, "invoice.pdf", "application/pdf")

    assert response.status_code == 422


def test_company_id_cannot_be_supplied_in_a_query_string(
    client: TestClient, two_companies: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    """The company comes from the path only; a query override is ignored."""
    a, b = two_companies

    listed = client.get(_documents_url(a["id"]), params={"company_id": b["id"]}).json()

    assert [d["original_filename"] for d in listed] == ["invoice-a.pdf"]