"""Ledger / vendor creation mirrors the master into TallyPrime (block semantics).

A ledger or vendor is persisted in PAL only after Tally accepts the ledger; a
duplicate name is rejected before Tally is contacted.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.integrations.tally import TallyError
from app.integrations.tally.parser import TallyImportResult
from app.integrations.tally.xml import build_ledger_import_request

COMPANIES_URL = "/api/v1/companies"


def _company(client: TestClient) -> dict[str, Any]:
    response = client.post(COMPANIES_URL, json={"name": "Sync Co"})
    assert response.status_code == 201, response.text
    return response.json()


def _rejecting(monkeypatch: pytest.MonkeyPatch, *, offline: bool = False) -> None:
    """Make every Tally ledger create fail: a rejection, or Tally unreachable."""

    class _Rejecting:
        def create_ledger(self, **kwargs):
            if offline:
                raise TallyError("Unable to connect to TallyPrime")
            return TallyImportResult(
                created=0, altered=0, ignored=0, errors=1, exceptions=0,
                cancelled=0, last_voucher_id=None, message="Group 'X' does not exist",
            )

        def close(self) -> None:
            pass

    monkeypatch.setattr("app.services.ledger.get_tally_service", lambda: _Rejecting())


def test_ledger_import_xml_carries_name_parent_and_gstin() -> None:
    xml = build_ledger_import_request(
        company="Sync Co", name="Benplat", parent="Sundry Creditors", gstin="29ABCDE1234F1Z5"
    )
    assert '<LEDGER NAME="Benplat" ACTION="Create">' in xml
    assert "<PARENT>Sundry Creditors</PARENT>" in xml
    assert "<PARTYGSTIN>29ABCDE1234F1Z5</PARTYGSTIN>" in xml
    assert "<SVCURRENTCOMPANY>Sync Co</SVCURRENTCOMPANY>" in xml


def test_ledger_import_xml_omits_gst_without_gstin() -> None:
    xml = build_ledger_import_request(company=None, name="Rent", parent="Indirect Expenses")
    assert "PARTYGSTIN" not in xml and "SVCURRENTCOMPANY" not in xml


def test_create_ledger_pushes_to_tally(client: TestClient, fake_tally_ledger_create) -> None:
    company = _company(client)
    response = client.post(
        f"{COMPANIES_URL}/{company['id']}/ledgers",
        json={"name": "Rent", "parent_group": "Indirect Expenses"},
    )
    assert response.status_code == 201, response.text
    assert fake_tally_ledger_create.created == [
        {"name": "Rent", "parent": "Indirect Expenses", "company": "Sync Co", "gstin": None}
    ]


def test_duplicate_ledger_is_rejected_before_tally(
    client: TestClient, fake_tally_ledger_create
) -> None:
    company = _company(client)
    url = f"{COMPANIES_URL}/{company['id']}/ledgers"
    assert client.post(url, json={"name": "Benplat"}).status_code == 201
    response = client.post(url, json={"name": "BENPLAT"})
    assert response.status_code == 409
    assert len(fake_tally_ledger_create.created) == 1  # second never reached Tally


@pytest.mark.parametrize("offline", [False, True])
def test_ledger_not_saved_when_tally_does_not_accept(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, offline: bool
) -> None:
    company = _company(client)
    _rejecting(monkeypatch, offline=offline)
    url = f"{COMPANIES_URL}/{company['id']}/ledgers"
    response = client.post(url, json={"name": "Rent"})
    assert response.status_code == 502
    assert client.get(url).json() == []  # block: nothing persisted in PAL


def test_create_vendor_creates_creditor_ledger_in_tally(
    client: TestClient, fake_tally_ledger_create
) -> None:
    company = _company(client)
    response = client.post(
        f"{COMPANIES_URL}/{company['id']}/vendors",
        json={"name": "Benplat", "gstin": "29ABCDE1234F1Z5"},
    )
    assert response.status_code == 201, response.text
    assert fake_tally_ledger_create.created == [
        {
            "name": "Benplat",
            "parent": "Sundry Creditors",
            "company": "Sync Co",
            "gstin": "29ABCDE1234F1Z5",
        }
    ]


def test_duplicate_vendor_name_is_rejected(
    client: TestClient, fake_tally_ledger_create
) -> None:
    company = _company(client)
    url = f"{COMPANIES_URL}/{company['id']}/vendors"
    assert client.post(url, json={"name": "Benplat"}).status_code == 201
    response = client.post(url, json={"name": " benplat "})
    assert response.status_code == 409
    assert len(fake_tally_ledger_create.created) == 1


def test_vendor_sync_imports_creditors_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, fake_tally_ledger_create
) -> None:
    from app.integrations.tally.parser import TallyLedger, TallyLedgerResult

    class _Ledgers:
        def get_ledgers(self, company):
            return TallyLedgerResult(
                company=company,
                ledgers=[
                    TallyLedger(name="BENPLAT", parent="Sundry Creditors", gstin="29ABCDE1234F1Z5"),
                    TallyLedger(name="New Supplier", parent="Sundry Creditors"),
                    TallyLedger(name="Rent", parent="Indirect Expenses"),
                ],
            )

        def close(self) -> None:
            pass

    monkeypatch.setattr("app.services.tally_sync.get_tally_service", lambda: _Ledgers())
    company = _company(client)
    url = f"{COMPANIES_URL}/{company['id']}/vendors"
    assert client.post(url, json={"name": "Benplat"}).status_code == 201

    sync_url = f"/api/v1/tally/sync/{company['id']}/vendors"
    body = client.post(sync_url, json={"tally_company_name": "Sync Co"}).json()
    assert (body["vendors_fetched"], body["vendors_created"], body["vendors_updated"]) == (2, 1, 1)
    again = client.post(sync_url, json={"tally_company_name": "Sync Co"}).json()
    assert (again["vendors_created"], again["vendors_unchanged"]) == (0, 2)

    vendors = {v["name"]: v for v in client.get(url).json()}
    assert set(vendors) == {"Benplat", "New Supplier"}  # no duplicate, no non-creditor
    assert vendors["Benplat"]["gstin"] == "29ABCDE1234F1Z5"


def test_vendor_not_saved_when_tally_rejects(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _company(client)
    _rejecting(monkeypatch)
    url = f"{COMPANIES_URL}/{company['id']}/vendors"
    response = client.post(url, json={"name": "Benplat"})
    assert response.status_code == 502
    assert client.get(url).json() == []
