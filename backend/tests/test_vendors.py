"""Tests for the Vendor Master, with emphasis on company-level isolation.

The isolation tests are the point of this step: they prove a vendor can only
ever be reached through the company that owns it.
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

COMPANIES_URL = "/api/v1/companies"

VALID_PAYLOAD: dict[str, Any] = {
    "name": "Sahyadri Supplies",
    "gstin": "27AAJCS4821K1Z5",
    "pan": "AAJCS4821K",
    "default_expense_ledger": "Freight Inward",
    "creditor_ledger": "Sahyadri Supplies",
    "parent_group": "Indirect Expenses",
}


def _company(client: TestClient, name: str = "Test Co") -> dict[str, Any]:
    response = client.post(COMPANIES_URL, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _vendors_url(company_id: Any) -> str:
    return f"{COMPANIES_URL}/{company_id}/vendors"


def _vendor(client: TestClient, company_id: Any, **overrides: Any) -> dict[str, Any]:
    response = client.post(_vendors_url(company_id), json={**VALID_PAYLOAD, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def two_companies(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    """Company A and Company B, each with one vendor."""
    a = _company(client, "Company A")
    b = _company(client, "Company B")
    _vendor(client, a["id"], name="Vendor A1")
    _vendor(client, b["id"], name="Vendor B1")
    return a, b


# --- 1 & 2. Create ---------------------------------------------------------


def test_create_vendor(client: TestClient) -> None:
    company = _company(client)

    created = _vendor(client, company["id"])

    assert uuid.UUID(created["id"])
    assert created["name"] == "Sahyadri Supplies"
    assert created["company_id"] == company["id"]
    assert created["status"] == "ACTIVE"
    assert created["default_expense_ledger"] == "Freight Inward"
    assert created["created_at"] and created["updated_at"]


def test_create_vendor_defaults_to_active(client: TestClient) -> None:
    company = _company(client)

    assert _vendor(client, company["id"])["status"] == "ACTIVE"


def test_create_vendor_with_explicit_status(client: TestClient) -> None:
    company = _company(client)

    assert _vendor(client, company["id"], status="NEEDS_MAPPING")["status"] == "NEEDS_MAPPING"


def test_create_vendor_only_requires_name(client: TestClient) -> None:
    company = _company(client)

    response = client.post(_vendors_url(company["id"]), json={"name": "Minimal Vendor"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["gstin"] is None
    assert body["parent_group"] is None


# --- 3 & 4. company_id is mandatory and not spoofable ----------------------


def test_create_vendor_rejects_nonexistent_company(client: TestClient) -> None:
    response = client.post(_vendors_url(uuid.uuid4()), json={"name": "Orphan"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Company not found"


def test_create_vendor_rejects_malformed_company_id(client: TestClient) -> None:
    """The company id comes from the path and must be a UUID."""
    response = client.post(_vendors_url("not-a-uuid"), json={"name": "Orphan"})

    assert response.status_code == 422


def test_create_vendor_rejects_company_id_in_body(client: TestClient) -> None:
    """company_id is path-owned; sending it in the body is rejected."""
    company = _company(client)

    response = client.post(
        _vendors_url(company["id"]),
        json={**VALID_PAYLOAD, "company_id": str(uuid.uuid4())},
    )

    assert response.status_code == 422, response.text


def test_list_vendors_rejects_nonexistent_company(client: TestClient) -> None:
    """Listing an unknown company is an empty result, not a cross-company leak."""
    response = client.get(_vendors_url(uuid.uuid4()))

    assert response.status_code == 200
    assert response.json() == []


# --- 5. List ---------------------------------------------------------------


def test_list_vendors_for_company(client: TestClient) -> None:
    company = _company(client)
    _vendor(client, company["id"], name="Vendor 1")
    _vendor(client, company["id"], name="Vendor 2")

    listed = client.get(_vendors_url(company["id"]))

    assert listed.status_code == 200
    assert {v["name"] for v in listed.json()} == {"Vendor 1", "Vendor 2"}


def test_list_vendors_is_empty_for_a_company_with_none(client: TestClient) -> None:
    company = _company(client)

    response = client.get(_vendors_url(company["id"]))

    assert response.status_code == 200
    assert response.json() == []


# --- 6. Get by id ----------------------------------------------------------


def test_get_vendor(client: TestClient) -> None:
    company = _company(client)
    created = _vendor(client, company["id"])

    response = client.get(f"{_vendors_url(company['id'])}/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


# --- 7 & 8. Update ---------------------------------------------------------


def test_update_vendor(client: TestClient) -> None:
    company = _company(client)
    created = _vendor(client, company["id"])

    response = client.patch(
        f"{_vendors_url(company['id'])}/{created['id']}",
        json={"name": "Renamed", "status": "NEEDS_MAPPING"},
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["name"] == "Renamed"
    assert updated["status"] == "NEEDS_MAPPING"
    assert updated["updated_at"] >= created["updated_at"]


def test_partial_update_touches_only_supplied_fields(client: TestClient) -> None:
    company = _company(client)
    created = _vendor(client, company["id"])

    response = client.patch(
        f"{_vendors_url(company['id'])}/{created['id']}",
        json={"parent_group": "Direct Expenses"},
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["parent_group"] == "Direct Expenses"
    assert updated["name"] == created["name"]
    assert updated["gstin"] == created["gstin"]
    assert updated["status"] == created["status"]
    assert updated["company_id"] == created["company_id"]
    assert updated["created_at"] == created["created_at"]


# --- 9, 10 & 11. Soft delete -----------------------------------------------


def test_deactivate_vendor_sets_status_to_inactive(client: TestClient) -> None:
    company = _company(client)
    created = _vendor(client, company["id"])

    response = client.delete(f"{_vendors_url(company['id'])}/{created['id']}")

    assert response.status_code == 200
    assert response.json()["status"] == "INACTIVE"

    # Still readable by id: the row was deactivated, not removed.
    assert (
        client.get(f"{_vendors_url(company['id'])}/{created['id']}").status_code == 200
    )


def test_inactive_vendors_are_excluded_by_default(client: TestClient) -> None:
    company = _company(client)
    keep = _vendor(client, company["id"], name="Keep")
    drop = _vendor(client, company["id"], name="Drop")

    client.delete(f"{_vendors_url(company['id'])}/{drop['id']}")

    listed = client.get(_vendors_url(company["id"])).json()
    assert [v["id"] for v in listed] == [keep["id"]]


def test_inactive_vendors_can_be_included(client: TestClient) -> None:
    company = _company(client)
    _vendor(client, company["id"], name="Keep")
    drop = _vendor(client, company["id"], name="Drop")

    client.delete(f"{_vendors_url(company['id'])}/{drop['id']}")

    listed = client.get(_vendors_url(company["id"]), params={"include_inactive": True}).json()
    assert {v["name"] for v in listed} == {"Keep", "Drop"}


def test_needs_mapping_vendors_stay_in_the_default_list(client: TestClient) -> None:
    """NEEDS_MAPPING is a live state, not a soft delete."""
    company = _company(client)
    _vendor(client, company["id"], name="Unmapped", status="NEEDS_MAPPING")

    listed = client.get(_vendors_url(company["id"])).json()

    assert [v["name"] for v in listed] == ["Unmapped"]


# --- 12. Vendor 404 --------------------------------------------------------


def test_get_nonexistent_vendor_returns_404(client: TestClient) -> None:
    company = _company(client)

    response = client.get(f"{_vendors_url(company['id'])}/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Vendor not found"


# --- 13 to 16. CROSS-COMPANY ISOLATION (the point of this step) -----------


def test_listing_a_company_never_returns_another_companys_vendors(
    client: TestClient, two_companies: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    a, b = two_companies

    listed_a = client.get(_vendors_url(a["id"])).json()
    listed_b = client.get(_vendors_url(b["id"])).json()

    assert [v["name"] for v in listed_a] == ["Vendor A1"]
    assert [v["name"] for v in listed_b] == ["Vendor B1"]
    assert not {v["name"] for v in listed_a} & {v["name"] for v in listed_b}


def test_cross_company_get_by_vendor_id_returns_404(
    client: TestClient, two_companies: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    """Company A asking for Company B's vendor sees nothing."""
    a, b = two_companies
    b_vendor = client.get(_vendors_url(b["id"])).json()[0]

    response = client.get(f"{_vendors_url(a['id'])}/{b_vendor['id']}")

    assert response.status_code == 404, response.text


def test_cross_company_patch_returns_404(
    client: TestClient, two_companies: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    a, b = two_companies
    b_vendor = client.get(_vendors_url(b["id"])).json()[0]

    response = client.patch(
        f"{_vendors_url(a['id'])}/{b_vendor['id']}", json={"name": "Hijacked"}
    )

    assert response.status_code == 404, response.text
    # And the other company's record is untouched.
    still_there = client.get(f"{_vendors_url(b['id'])}/{b_vendor['id']}").json()
    assert still_there["name"] == "Vendor B1"


def test_cross_company_delete_returns_404(
    client: TestClient, two_companies: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    a, b = two_companies
    b_vendor = client.get(_vendors_url(b["id"])).json()[0]

    response = client.delete(f"{_vendors_url(a['id'])}/{b_vendor['id']}")

    assert response.status_code == 404, response.text
    assert (
        client.get(f"{_vendors_url(b['id'])}/{b_vendor['id']}").json()["status"]
        != "INACTIVE"
    )


def test_include_inactive_does_not_leak_across_companies(
    client: TestClient, two_companies: tuple[dict[str, Any], dict[str, Any]]
) -> None:
    """include_inactive widens status, never company scope."""
    a, b = two_companies
    b_vendor = client.get(_vendors_url(b["id"])).json()[0]
    client.delete(f"{_vendors_url(b['id'])}/{b_vendor['id']}")

    listed_a = client.get(
        _vendors_url(a["id"]), params={"include_inactive": True}
    ).json()

    assert [v["name"] for v in listed_a] == ["Vendor A1"]


# --- 17 & 18. Tax id normalisation ----------------------------------------


def test_create_normalises_gstin_and_pan(client: TestClient) -> None:
    company = _company(client)

    created = _vendor(client, company["id"], gstin="  27aajcs4821k1z5 ", pan=" aajcs4821k ")

    assert created["gstin"] == "27AAJCS4821K1Z5"
    assert created["pan"] == "AAJCS4821K"


def test_patch_normalises_gstin_and_pan(client: TestClient) -> None:
    company = _company(client)
    created = _vendor(client, company["id"])

    response = client.patch(
        f"{_vendors_url(company['id'])}/{created['id']}", json={"pan": "aarfn7310m"}
    )

    assert response.json()["pan"] == "AARFN7310M"


def test_blank_tax_ids_become_null(client: TestClient) -> None:
    company = _company(client)

    created = _vendor(client, company["id"], gstin="", pan="   ")

    assert created["gstin"] is None
    assert created["pan"] is None


# --- 19. Validation --------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected_field"),
    [
        ({"name": ""}, "name"),
        ({"name": "   "}, "name"),
        ({"gstin": "INVALID"}, "gstin"),
        ({"pan": "abc123"}, "pan"),
    ],
)
def test_create_rejects_invalid_payloads(
    client: TestClient, payload: dict[str, Any], expected_field: str
) -> None:
    company = _company(client)

    response = client.post(_vendors_url(company["id"]), json={**VALID_PAYLOAD, **payload})

    assert response.status_code == 422, response.text
    assert any(expected_field in error["loc"] for error in response.json()["detail"])


@pytest.mark.parametrize("bad_status", ["active", "ARCHIVED", "Inactive", ""])
def test_create_rejects_invalid_status(
    client: TestClient, bad_status: str
) -> None:
    company = _company(client)

    response = client.post(
        _vendors_url(company["id"]), json={**VALID_PAYLOAD, "status": bad_status}
    )

    assert response.status_code == 422, response.text


def test_patch_rejects_invalid_status(client: TestClient) -> None:
    company = _company(client)
    created = _vendor(client, company["id"])

    response = client.patch(
        f"{_vendors_url(company['id'])}/{created['id']}", json={"status": "ARCHIVED"}
    )

    assert response.status_code == 422, response.text


# --- 20. Nullable field clearing ------------------------------------------


def test_patch_can_clear_a_nullable_field(client: TestClient) -> None:
    company = _company(client)
    created = _vendor(client, company["id"])

    response = client.patch(
        f"{_vendors_url(company['id'])}/{created['id']}",
        json={"pan": None, "parent_group": None},
    )

    assert response.status_code == 200
    assert response.json()["pan"] is None
    assert response.json()["parent_group"] is None