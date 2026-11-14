"""Tests for the per-company Settings document API."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.company import Company
from app.schemas.company_settings import MAX_SETTINGS_BYTES


def _company(db: Session) -> Company:
    company = Company(name="Settings Co", gstin="27AAJCS4821K1Z5")
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def _url(company_id) -> str:
    return f"/api/v1/companies/{company_id}/settings"


def test_get_before_save_returns_empty(client: TestClient, db: Session) -> None:
    company = _company(db)
    resp = client.get(_url(company.id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == {}
    assert body["updated_at"] is None


def test_save_then_read_roundtrips(client: TestClient, db: Session) -> None:
    company = _company(db)
    payload = {"firm": "PAL LLP", "mfa": True, "session": "30"}

    put = client.put(_url(company.id), json={"data": payload})
    assert put.status_code == 200
    assert put.json()["data"] == payload
    assert put.json()["updated_at"] is not None

    get = client.get(_url(company.id))
    assert get.json()["data"] == payload


def test_save_replaces_whole_document(client: TestClient, db: Session) -> None:
    company = _company(db)
    client.put(_url(company.id), json={"data": {"a": "1", "b": "2"}})
    client.put(_url(company.id), json={"data": {"a": "9"}})
    assert client.get(_url(company.id)).json()["data"] == {"a": "9"}


def test_unknown_company_is_404(client: TestClient) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(_url(missing)).status_code == 404
    assert client.put(_url(missing), json={"data": {}}).status_code == 404


def test_oversized_document_is_rejected(client: TestClient, db: Session) -> None:
    company = _company(db)
    big = {"blob": "x" * (MAX_SETTINGS_BYTES + 1)}
    resp = client.put(_url(company.id), json={"data": big})
    assert resp.status_code == 422
