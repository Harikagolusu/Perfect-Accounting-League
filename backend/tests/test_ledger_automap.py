"""Ledger name matching and the Tally auto-map.

A Tally chart of accounts rarely spells a party the way an invoice prints it,
so these tests pin the tolerance (abbreviation, punctuation, a small typo) and,
just as importantly, the refusals: an ambiguous or distant name must stay
unmapped rather than post against the wrong party.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.ledger import Ledger
from app.services.ledger import is_posting_head_candidate, match_ledger_by_name
from app.services.tally_sync import (
    _automap_company_ledgers,
    _automap_vendor_ledgers,
)

COMPANIES_URL = "/api/v1/companies"


def _company(client: TestClient, name: str = "Automap Co", **overrides: Any) -> dict[str, Any]:
    response = client.post(COMPANIES_URL, json={"name": name, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def _ledger(
    client: TestClient, company_id: str, name: str, parent_group: str | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name}
    if parent_group:
        body["parent_group"] = parent_group
    response = client.post(f"{COMPANIES_URL}/{company_id}/ledgers", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _vendor(client: TestClient, company_id: str, name: str, creditor: str | None) -> dict[str, Any]:
    response = client.post(
        f"{COMPANIES_URL}/{company_id}/vendors",
        json={"name": name, "creditor_ledger": creditor},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_posting_head_candidate_excludes_non_heads(
    client: TestClient, db: Session
) -> None:
    """Narrowing the choices for a ledger decision, by group and by name."""
    company = _company(client)
    cases = {
        # (name, group): is a plausible expense/income head?
        ("Freight Inward", "Indirect Expenses"): True,
        ("Office Rent_Mumbai", "Rent Expenses"): True,
        ("Taxi Fare", "Indirect Expenses"): True,
        ("Brokerage", "Sales Accounts"): True,
        ("Some Supplier Pvt Ltd", "Sundry Creditors"): False,
        ("A Customer Ltd", "Sundry Debtors"): False,
        ("Vendor X", "Creditor for Expenses"): False,
        ("HDFC Current", "Bank Accounts"): False,
        ("Petty Cash", "Cash-in-Hand"): False,
        ("Output CGST_KA", "GST_KA"): False,
        ("TDS on Rent", "TDS"): False,
        ("Round Off", "General and Administration Expenses"): False,
        ("Laptops", "Fixed Assets"): False,
        ("PF Payable", "PF Payable"): False,
        ("Share Capital", "Equity Share Capital"): False,
    }
    for (name, group), expected in cases.items():
        row = _ledger(client, company["id"], name, group)
        ledger = db.get(Ledger, uuid.UUID(row["id"]))
        assert is_posting_head_candidate(ledger) is expected, f"{name} ({group})"


def test_exact_name_wins(client: TestClient, db: Session) -> None:
    company = _company(client)
    _ledger(client, company["id"], "Freight Inward")

    match = match_ledger_by_name(db, uuid.UUID(company["id"]), "freight inward")
    assert match is not None and match.name == "Freight Inward"


def test_abbreviated_company_suffix_matches(client: TestClient, db: Session) -> None:
    company = _company(client)
    _ledger(client, company["id"], "MULTISOFT INFOCOM PVT LTD", "Sundry Creditors")

    match = match_ledger_by_name(
        db,
        uuid.UUID(company["id"]),
        "MULTISOFT INFOCOM PRIVATE LIMITED",
        parent_group="Sundry Creditors",
    )
    assert match is not None and match.name == "MULTISOFT INFOCOM PVT LTD"


def test_small_spelling_difference_matches(client: TestClient, db: Session) -> None:
    """The real case: Tally holds 'DWCON' where the invoice prints 'DEVCON'."""
    company = _company(client)
    _ledger(client, company["id"], "RADHIKA DWCON PVT LTD", "Sundry Creditors")

    match = match_ledger_by_name(
        db,
        uuid.UUID(company["id"]),
        "RADHIKA DEVCON PRIVATE LIMITED",
        parent_group="Sundry Creditors",
    )
    assert match is not None and match.name == "RADHIKA DWCON PVT LTD"


def test_suffix_identical_name_beats_a_similar_neighbour(
    client: TestClient, db: Session
) -> None:
    """Only the abbreviation differs, so this is a certainty, not a guess."""
    company = _company(client)
    _ledger(client, company["id"], "RADHIKA DEVCON PVT LTD", "Sundry Creditors")
    _ledger(client, company["id"], "RADHIKA DEVCOM PVT LTD", "Sundry Creditors")

    match = match_ledger_by_name(
        db,
        uuid.UUID(company["id"]),
        "RADHIKA DEVCON PRIVATE LIMITED",
        parent_group="Sundry Creditors",
    )
    assert match is not None and match.name == "RADHIKA DEVCON PVT LTD"


def test_two_equally_close_ledgers_stay_unmatched(
    client: TestClient, db: Session
) -> None:
    """Neither is an exact key match, so picking either could be wrong."""
    company = _company(client)
    _ledger(client, company["id"], "RADHIKA DWCON PVT LTD", "Sundry Creditors")
    _ledger(client, company["id"], "RADHIKA DEVCOM PVT LTD", "Sundry Creditors")

    assert (
        match_ledger_by_name(
            db,
            uuid.UUID(company["id"]),
            "RADHIKA DEVCON PRIVATE LIMITED",
            parent_group="Sundry Creditors",
        )
        is None
    )


def test_unrelated_name_stays_unmatched(client: TestClient, db: Session) -> None:
    company = _company(client)
    _ledger(client, company["id"], "Freight Inward", "Direct Expenses")

    assert (
        match_ledger_by_name(
            db,
            uuid.UUID(company["id"]),
            "RADHIKA DEVCON PRIVATE LIMITED",
            parent_group="Sundry Creditors",
        )
        is None
    )


def test_parent_group_confines_the_match(client: TestClient, db: Session) -> None:
    """A party name must never land on an expense ledger."""
    company = _company(client)
    _ledger(client, company["id"], "RADHIKA DWCON PVT LTD", "Direct Expenses")

    assert (
        match_ledger_by_name(
            db,
            uuid.UUID(company["id"]),
            "RADHIKA DEVCON PRIVATE LIMITED",
            parent_group="Sundry Creditors",
        )
        is None
    )


def test_company_tax_ledgers_are_filled_from_the_master(
    client: TestClient, db: Session
) -> None:
    company = _company(client)
    for name in ("CGST Input", "SGST Input", "IGST Input", "Round Off", "Sales"):
        _ledger(client, company["id"], name)

    filled = _automap_company_ledgers(db, uuid.UUID(company["id"]))
    db.commit()

    assert filled == 5
    row = client.get(f"{COMPANIES_URL}/{company['id']}").json()
    assert row["cgst_ledger"] == "CGST Input"
    assert row["sgst_ledger"] == "SGST Input"
    assert row["igst_ledger"] == "IGST Input"
    assert row["round_off_ledger"] == "Round Off"
    assert row["sales_ledger"] == "Sales"
    # Nothing in the master matches Cess, so it stays for a person to choose.
    assert row["cess_ledger"] is None


def test_automap_does_not_overwrite_a_configured_ledger(
    client: TestClient, db: Session
) -> None:
    company = _company(client, cgst_ledger="Output CGST_KA")
    _ledger(client, company["id"], "CGST Input")

    _automap_company_ledgers(db, uuid.UUID(company["id"]))
    db.commit()

    row = client.get(f"{COMPANIES_URL}/{company['id']}").json()
    assert row["cgst_ledger"] == "Output CGST_KA"


def test_vendor_creditor_ledger_is_repointed_at_the_tally_name(
    client: TestClient, db: Session
) -> None:
    company = _company(client)
    _ledger(client, company["id"], "RADHIKA DWCON PVT LTD", "Sundry Creditors")
    vendor = _vendor(
        client,
        company["id"],
        "RADHIKA DEVCON PRIVATE LIMITED",
        "RADHIKA DEVCON PRIVATE LIMITED",
    )

    remapped = _automap_vendor_ledgers(db, uuid.UUID(company["id"]))
    db.commit()

    assert remapped == 1
    row = client.get(f"{COMPANIES_URL}/{company['id']}/vendors/{vendor['id']}").json()
    assert row["creditor_ledger"] == "RADHIKA DWCON PVT LTD"


def test_vendor_ledger_that_already_resolves_is_left_alone(
    client: TestClient, db: Session
) -> None:
    company = _company(client)
    _ledger(client, company["id"], "Kalash Travels", "Sundry Creditors")
    _ledger(client, company["id"], "Kalash Travel Services", "Sundry Creditors")
    vendor = _vendor(client, company["id"], "Kalash Travels", "Kalash Travels")

    assert _automap_vendor_ledgers(db, uuid.UUID(company["id"])) == 0
    db.commit()
    row = client.get(f"{COMPANIES_URL}/{company['id']}/vendors/{vendor['id']}").json()
    assert row["creditor_ledger"] == "Kalash Travels"
