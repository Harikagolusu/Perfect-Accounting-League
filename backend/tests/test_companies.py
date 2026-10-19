"""Tests for the Companies & Clients endpoints."""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

COMPANIES_URL = "/api/v1/companies"

VALID_PAYLOAD: dict[str, Any] = {
    "name": "Sahyadri Agro",
    "legal_name": "Sahyadri Agro Foods Private Limited",
    "gstin": "27AAJCS4821K1Z5",
    "pan": "AAJCS4821K",
    "email": "accounts@sahyadri-demo.in",
    "phone": "+91 98220 11223",
    "address": "Plot 14, MIDC Bhosari, Pune 411026",
    "financial_year": "FY 2026-27",
}


def _create(client: TestClient, **overrides: Any) -> dict[str, Any]:
    """POST a company and return the created body."""
    response = client.post(COMPANIES_URL, json={**VALID_PAYLOAD, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def _without_name() -> dict[str, Any]:
    """VALID_PAYLOAD with the required `name` key removed entirely."""
    return {k: v for k, v in VALID_PAYLOAD.items() if k != "name"}


def _id_of(companies: list[dict[str, Any]], name: str) -> str:
    match = next(c for c in companies if c["name"] == name)
    return match["id"]


# --- 1. Create with each status -------------------------------------------


def test_create_active_company(client: TestClient) -> None:
    created = _create(client, status="ACTIVE")

    assert uuid.UUID(created["id"])
    assert created["status"] == "ACTIVE"
    assert created["name"] == "Sahyadri Agro"
    assert created["legal_name"] == "Sahyadri Agro Foods Private Limited"
    assert created["gstin"] == "27AAJCS4821K1Z5"
    assert created["created_at"] and created["updated_at"]
    # The boolean is gone; status is the single source of truth.
    assert "is_active" not in created


def test_create_onboarding_company(client: TestClient) -> None:
    assert _create(client, status="ONBOARDING")["status"] == "ONBOARDING"


def test_create_inactive_company(client: TestClient) -> None:
    """INACTIVE is accepted on create; it is the soft-delete marker."""
    assert _create(client, status="INACTIVE")["status"] == "INACTIVE"


# --- 2. Default status -----------------------------------------------------


def test_create_defaults_to_active(client: TestClient) -> None:
    assert _create(client)["status"] == "ACTIVE"


# --- 3. List ---------------------------------------------------------------


def test_list_excludes_inactive_by_default(client: TestClient) -> None:
    _create(client, name="Active Co", status="ACTIVE")
    _create(client, name="Onboarding Co", status="ONBOARDING")
    _create(client, name="Inactive Co", status="INACTIVE")

    listed = client.get(COMPANIES_URL)

    assert listed.status_code == 200
    names = {c["name"] for c in listed.json()}
    # ACTIVE and ONBOARDING are both live states and must both appear.
    assert names == {"Active Co", "Onboarding Co"}


def test_list_can_include_inactive(client: TestClient) -> None:
    _create(client, name="Active Co", status="ACTIVE")
    _create(client, name="Inactive Co", status="INACTIVE")

    listed = client.get(COMPANIES_URL, params={"include_inactive": True})

    assert {c["name"] for c in listed.json()} == {"Active Co", "Inactive Co"}


def test_soft_deleted_company_leaves_the_default_list(client: TestClient) -> None:
    created = _create(client, name="Active Co")
    _create(client, name="Onboarding Co", status="ONBOARDING")

    assert client.delete(f"{COMPANIES_URL}/{created['id']}").status_code == 200

    listed = client.get(COMPANIES_URL)

    assert {c["name"] for c in listed.json()} == {"Onboarding Co"}


# --- 4. Get by id ----------------------------------------------------------


def test_get_company_by_id(client: TestClient) -> None:
    created = _create(client)

    response = client.get(f"{COMPANIES_URL}/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


# --- 5. Update -------------------------------------------------------------


def test_update_company_status(client: TestClient) -> None:
    created = _create(client, status="ONBOARDING")

    response = client.patch(
        f"{COMPANIES_URL}/{created['id']}", json={"status": "ACTIVE"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ACTIVE"


def test_update_normal_fields(client: TestClient) -> None:
    created = _create(client)

    response = client.patch(
        f"{COMPANIES_URL}/{created['id']}",
        json={"name": "Sahyadri Agro Ltd", "email": "new@sahyadri-demo.in"},
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["name"] == "Sahyadri Agro Ltd"
    assert updated["email"] == "new@sahyadri-demo.in"
    assert updated["updated_at"] >= created["updated_at"]


def test_partial_patch_touches_only_supplied_fields(client: TestClient) -> None:
    created = _create(client)

    response = client.patch(
        f"{COMPANIES_URL}/{created['id']}", json={"phone": "+91 90000 00000"}
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["phone"] == "+91 90000 00000"
    assert updated["name"] == created["name"]
    assert updated["gstin"] == created["gstin"]
    assert updated["status"] == created["status"]
    assert updated["created_at"] == created["created_at"]


# --- 6. Soft delete --------------------------------------------------------


def test_delete_company_sets_status_to_inactive(client: TestClient) -> None:
    created = _create(client)

    response = client.delete(f"{COMPANIES_URL}/{created['id']}")

    assert response.status_code == 200
    assert response.json()["status"] == "INACTIVE"

    # Still readable by id: the row was deactivated, not removed.
    assert client.get(f"{COMPANIES_URL}/{created['id']}").status_code == 200


def test_delete_company_does_not_remove_the_row(client: TestClient) -> None:
    created = _create(client)

    client.delete(f"{COMPANIES_URL}/{created['id']}")

    everything = client.get(COMPANIES_URL, params={"include_inactive": True}).json()
    assert created["id"] in {c["id"] for c in everything}


# --- 7. Not found ----------------------------------------------------------


def test_get_nonexistent_company_returns_404(client: TestClient) -> None:
    response = client.get(f"{COMPANIES_URL}/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Company not found"


def test_delete_nonexistent_company_returns_404(client: TestClient) -> None:
    assert client.delete(f"{COMPANIES_URL}/{uuid.uuid4()}").status_code == 404


def test_patch_nonexistent_company_returns_404(client: TestClient) -> None:
    response = client.patch(f"{COMPANIES_URL}/{uuid.uuid4()}", json={"name": "Nope"})

    assert response.status_code == 404


# --- 8. Invalid status -----------------------------------------------------


@pytest.mark.parametrize("bad_status", ["active", "ARCHIVED", "Onboarding", "", "1"])
def test_create_rejects_invalid_status(client: TestClient, bad_status: str) -> None:
    response = client.post(COMPANIES_URL, json={**VALID_PAYLOAD, "status": bad_status})

    assert response.status_code == 422, response.text
    assert any("status" in error["loc"] for error in response.json()["detail"])


def test_patch_rejects_invalid_status(client: TestClient) -> None:
    created = _create(client)

    response = client.patch(
        f"{COMPANIES_URL}/{created['id']}", json={"status": "ARCHIVED"}
    )

    assert response.status_code == 422, response.text


# --- 9. GSTIN / PAN normalisation -----------------------------------------


def test_create_normalises_tax_ids(client: TestClient) -> None:
    created = _create(client, gstin="  27aajcs4821k1z5 ", pan=" aajcs4821k ")

    assert created["gstin"] == "27AAJCS4821K1Z5"
    assert created["pan"] == "AAJCS4821K"


def test_patch_normalises_tax_ids(client: TestClient) -> None:
    created = _create(client)

    response = client.patch(
        f"{COMPANIES_URL}/{created['id']}", json={"pan": "aarfn7310m"}
    )

    assert response.json()["pan"] == "AARFN7310M"


def test_blank_tax_ids_become_null(client: TestClient) -> None:
    created = _create(client, gstin="", pan="   ")

    assert created["gstin"] is None
    assert created["pan"] is None


# --- 10. Nullable field clearing ------------------------------------------


def test_patch_can_clear_a_nullable_field(client: TestClient) -> None:
    created = _create(client)

    response = client.patch(f"{COMPANIES_URL}/{created['id']}", json={"pan": None})

    assert response.status_code == 200
    assert response.json()["pan"] is None


# --- 11. Validation --------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected_field"),
    [
        ({**VALID_PAYLOAD, "name": ""}, "name"),
        ({**VALID_PAYLOAD, "name": "   "}, "name"),
        (_without_name(), "name"),
        ({**VALID_PAYLOAD, "email": "not-an-email"}, "email"),
        ({**VALID_PAYLOAD, "gstin": "INVALID"}, "gstin"),
        ({**VALID_PAYLOAD, "pan": "abc123"}, "pan"),
    ],
)
def test_create_rejects_invalid_payloads(
    client: TestClient, payload: dict[str, Any], expected_field: str
) -> None:
    response = client.post(COMPANIES_URL, json=payload)

    assert response.status_code == 422, response.text
    assert any(expected_field in error["loc"] for error in response.json()["detail"])


def test_create_only_requires_name(client: TestClient) -> None:
    response = client.post(COMPANIES_URL, json={"name": "Minimal Co"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["legal_name"] is None


def test_create_rejects_unknown_fields(client: TestClient) -> None:
    """Frontend-only concepts must not leak into the API contract."""
    response = client.post(
        COMPANIES_URL, json={**VALID_PAYLOAD, "assigned_users": ["Ananya Rao"]}
    )

    assert response.status_code == 422, response.text