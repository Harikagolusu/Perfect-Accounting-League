"""Tests for the Bank Statements foundation.

Three things carry the weight here:

* an account, statement or transaction can only ever be reached through the
  company that owns it, and can only point at records of that same company;
* money never touches binary floating point;
* nothing pretends to have parsed a statement, because no parser exists.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.bank import BankAccount, BankStatement, BankTransaction
from app.services.bank import statement_review_issues
from app.services.bank_extraction import EXTRACTION_NOT_CONFIGURED
from app.services.bank_validation import (
    BankTransitionError,
    BankValidationError,
    assert_transition_allowed,
    reconcile_balances,
    reject_if_fatal,
    validate_statement,
    validate_transaction,
)

COMPANIES_URL = "/api/v1/companies"
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"

#: Chosen so its masked form cannot contain the original as a substring.
SECRET_ACCOUNT_NUMBER = "50100234567890"


def _base(company_id: Any) -> str:
    return f"{COMPANIES_URL}/{company_id}"


def _accounts(company_id: Any) -> str:
    return f"{_base(company_id)}/bank-accounts"


def _statements(company_id: Any) -> str:
    return f"{_base(company_id)}/bank-statements"


def _company(client: TestClient, name: str = "Test Co") -> dict[str, Any]:
    response = client.post(COMPANIES_URL, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _document(client: TestClient, company_id: str) -> dict[str, Any]:
    response = client.post(
        f"{_base(company_id)}/documents",
        files={"file": ("statement.pdf", PDF_BYTES, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _account(
    client: TestClient,
    company_id: str,
    *,
    bank_name: str = "HDFC Bank",
    number: str = SECRET_ACCOUNT_NUMBER,
) -> dict[str, Any]:
    response = client.post(
        _accounts(company_id),
        json={"bank_name": bank_name, "account_number": number, "account_name": "Current"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _statement(
    client: TestClient,
    company_id: str,
    account_id: str,
    **overrides: Any,
) -> Any:
    payload: dict[str, Any] = {
        "bank_account_id": account_id,
        "statement_start_date": "2026-03-01",
        "statement_end_date": "2026-03-31",
        "opening_balance": "10000.00",
        "closing_balance": "10500.00",
    }
    payload.update(overrides)
    return client.post(_statements(company_id), json=payload)


def _transaction(
    client: TestClient, company_id: str, statement_id: str, **overrides: Any
) -> Any:
    payload: dict[str, Any] = {
        "transaction_date": "2026-03-10",
        "description": "NEFT CR ACME LTD",
        "debit_amount": "0.00",
        "credit_amount": "2000.00",
    }
    payload.update(overrides)
    return client.post(f"{_statements(company_id)}/{statement_id}/transactions", json=payload)


# --- Bank accounts ---------------------------------------------------------


def test_create_bank_account_returns_masked_number(client: TestClient) -> None:
    company = _company(client)
    response = client.post(
        _accounts(company["id"]),
        json={"bank_name": "HDFC Bank", "account_number": SECRET_ACCOUNT_NUMBER},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["masked_account_number"].endswith("7890")
    assert body["masked_account_number"].startswith("X")
    assert body["company_id"] == company["id"]
    assert body["currency"] == "INR"
    assert body["is_active"] is True


def test_full_account_number_never_appears_in_any_response(client: TestClient) -> None:
    """The full number is accepted on write and must not come back on read."""
    company = _company(client)
    created = _account(client, company["id"])
    assert SECRET_ACCOUNT_NUMBER not in created["masked_account_number"]

    listed = client.get(_accounts(company["id"]))
    assert listed.status_code == 200
    assert SECRET_ACCOUNT_NUMBER not in listed.text

    fetched = client.get(f"{_accounts(company['id'])}/{created['id']}")
    assert fetched.status_code == 200
    assert SECRET_ACCOUNT_NUMBER not in fetched.text

    # Not present in any field name either: there is no field carrying it.
    assert "account_number" not in created


def test_bank_account_number_is_persisted_in_full(client: TestClient, db: Session) -> None:
    """Masking is a display concern only; the stored value stays complete."""
    company = _company(client)
    created = _account(client, company["id"])
    row = db.get(BankAccount, uuid.UUID(created["id"]))
    assert row is not None
    assert row.account_number == SECRET_ACCOUNT_NUMBER


def test_list_and_get_bank_account(client: TestClient) -> None:
    company = _company(client)
    first = _account(client, company["id"], bank_name="HDFC Bank")
    _account(client, company["id"], bank_name="ICICI Bank", number="99998888777766")

    listed = client.get(_accounts(company["id"]))
    assert listed.status_code == 200
    assert len(listed.json()) == 2

    fetched = client.get(f"{_accounts(company['id'])}/{first['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["bank_name"] == "HDFC Bank"


def test_update_bank_account_applies_only_supplied_fields(client: TestClient) -> None:
    company = _company(client)
    created = _account(client, company["id"])
    response = client.patch(
        f"{_accounts(company['id'])}/{created['id']}",
        json={"bank_name": "Renamed Bank"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bank_name"] == "Renamed Bank"
    assert body["account_name"] == "Current"  # untouched
    assert body["masked_account_number"].endswith("7890")


def test_delete_bank_account_deactivates_it(client: TestClient) -> None:
    """Accounts are deactivated, not removed: statements still reference them."""
    company = _company(client)
    created = _account(client, company["id"])
    response = client.delete(f"{_accounts(company['id'])}/{created['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False
    assert client.get(f"{_accounts(company['id'])}/{created['id']}").status_code == 200


def test_bank_account_company_isolation(client: TestClient) -> None:
    a = _company(client, "Company A")
    b = _company(client, "Company B")
    account_b = _account(client, b["id"])

    # B's account never appears in A's listing.
    assert _account(client, a["id"], number="11112222333344")["id"] != account_b["id"]
    listed_a = client.get(_accounts(a["id"])).json()
    assert [row["id"] for row in listed_a] != [account_b["id"]]

    assert client.get(f"{_accounts(a['id'])}/{account_b['id']}").status_code == 404
    assert client.patch(
        f"{_accounts(a['id'])}/{account_b['id']}", json={"bank_name": "Hijacked"}
    ).status_code == 404
    assert client.delete(f"{_accounts(a['id'])}/{account_b['id']}").status_code == 404

    # Untouched by the cross-company attempts.
    assert client.get(f"{_accounts(b['id'])}/{account_b['id']}").json()["bank_name"] == "HDFC Bank"


# --- Bank statements -------------------------------------------------------


def test_create_statement_starts_uploaded_with_no_transactions(client: TestClient) -> None:
    company = _company(client)
    account = _account(client, company["id"])
    response = _statement(client, company["id"], account["id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "UPLOADED"
    assert body["opening_balance"] == "10000.00"
    assert body["closing_balance"] == "10500.00"
    assert body["bank_account_id"] == account["id"]

    listed = client.get(f"{_statements(company['id'])}/{body['id']}/transactions")
    assert listed.status_code == 200
    assert listed.json() == []


def test_create_statement_with_source_document(client: TestClient) -> None:
    company = _company(client)
    account = _account(client, company["id"])
    document = _document(client, company["id"])
    response = _statement(client, company["id"], account["id"], document_id=document["id"])
    assert response.status_code == 201, response.text
    assert response.json()["document_id"] == document["id"]


def test_list_and_get_statement(client: TestClient) -> None:
    company = _company(client)
    account = _account(client, company["id"])
    created = _statement(client, company["id"], account["id"]).json()

    listed = client.get(_statements(company["id"]))
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [created["id"]]

    fetched = client.get(f"{_statements(company['id'])}/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["statement_start_date"] == "2026-03-01"
    assert fetched.json()["statement_end_date"] == "2026-03-31"


def test_list_statements_filtered_by_account_and_status(client: TestClient) -> None:
    company = _company(client)
    account_a = _account(client, company["id"], bank_name="HDFC Bank")
    account_b = _account(client, company["id"], bank_name="ICICI Bank", number="99998888777766")
    _statement(client, company["id"], account_a["id"])
    _statement(client, company["id"], account_b["id"])

    filtered = client.get(
        _statements(company["id"]), params={"bank_account_id": account_a["id"]}
    )
    assert filtered.status_code == 200
    assert len(filtered.json()) == 1

    by_status = client.get(_statements(company["id"]), params={"status": "UPLOADED"})
    assert len(by_status.json()) == 2
    missing = client.get(_statements(company["id"]), params={"status": "PROCESSED"})
    assert missing.json() == []


def test_update_statement_fields(client: TestClient) -> None:
    company = _company(client)
    account = _account(client, company["id"])
    created = _statement(client, company["id"], account["id"]).json()

    response = client.patch(
        f"{_statements(company['id'])}/{created['id']}",
        json={"closing_balance": "12345.67", "statement_end_date": "2026-04-30"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["closing_balance"] == "12345.67"
    assert body["statement_end_date"] == "2026-04-30"
    assert body["opening_balance"] == "10000.00"  # untouched


def test_invalid_date_range_is_rejected(client: TestClient) -> None:
    """An inverted period is not a period; it is never silently swapped."""
    company = _company(client)
    account = _account(client, company["id"])
    response = _statement(
        client,
        company["id"],
        account["id"],
        statement_start_date="2026-03-31",
        statement_end_date="2026-03-01",
    )
    assert response.status_code == 422, response.text
    assert "before the start date" in response.json()["detail"]
    assert client.get(_statements(company["id"])).json() == []


def test_patch_that_would_invert_period_is_rejected_and_row_unchanged(client: TestClient) -> None:
    company = _company(client)
    account = _account(client, company["id"])
    created = _statement(client, company["id"], account["id"]).json()

    response = client.patch(
        f"{_statements(company['id'])}/{created['id']}",
        json={"statement_start_date": "2026-06-01"},
    )
    assert response.status_code == 422, response.text

    after = client.get(f"{_statements(company['id'])}/{created['id']}").json()
    assert after["statement_start_date"] == "2026-03-01"


def test_patch_cannot_detach_the_bank_account(client: TestClient) -> None:
    company = _company(client)
    account = _account(client, company["id"])
    created = _statement(client, company["id"], account["id"]).json()

    response = client.patch(
        f"{_statements(company['id'])}/{created['id']}", json={"bank_account_id": None}
    )
    assert response.status_code == 422, response.text
    assert client.get(
        f"{_statements(company['id'])}/{created['id']}"
    ).json()["bank_account_id"] == account["id"]


def test_illegal_status_transition_is_rejected(client: TestClient) -> None:
    """UPLOADED cannot jump straight to PROCESSED or FAILED."""
    company = _company(client)
    account = _account(client, company["id"])
    created = _statement(client, company["id"], account["id"]).json()

    for target in ("PROCESSED", "FAILED"):
        response = client.patch(
            f"{_statements(company['id'])}/{created['id']}", json={"status": target}
        )
        assert response.status_code == 409, response.text

    allowed = client.patch(
        f"{_statements(company['id'])}/{created['id']}", json={"status": "NEEDS_REVIEW"}
    )
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "NEEDS_REVIEW"


def test_create_statement_rejects_cross_company_account(client: TestClient) -> None:
    a = _company(client, "Company A")
    b = _company(client, "Company B")
    account_b = _account(client, b["id"])

    response = _statement(client, a["id"], account_b["id"])
    assert response.status_code == 404, response.text
    assert client.get(_statements(a["id"])).json() == []


def test_create_statement_rejects_cross_company_document(client: TestClient) -> None:
    a = _company(client, "Company A")
    b = _company(client, "Company B")
    account_a = _account(client, a["id"])
    document_b = _document(client, b["id"])

    response = _statement(client, a["id"], account_a["id"], document_id=document_b["id"])
    assert response.status_code == 404, response.text
    assert client.get(_statements(a["id"])).json() == []


def test_patch_rejects_cross_company_document(client: TestClient) -> None:
    a = _company(client, "Company A")
    b = _company(client, "Company B")
    account_a = _account(client, a["id"])
    created = _statement(client, a["id"], account_a["id"]).json()
    document_b = _document(client, b["id"])

    response = client.patch(
        f"{_statements(a['id'])}/{created['id']}", json={"document_id": document_b["id"]}
    )
    assert response.status_code == 404, response.text
    assert client.get(
        f"{_statements(a['id'])}/{created['id']}"
    ).json()["document_id"] is None


def test_statement_company_isolation_get_patch_delete(client: TestClient) -> None:
    a = _company(client, "Company A")
    b = _company(client, "Company B")
    account_b = _account(client, b["id"])
    statement_b = _statement(client, b["id"], account_b["id"]).json()

    assert client.get(f"{_statements(a['id'])}/{statement_b['id']}").status_code == 404
    assert client.patch(
        f"{_statements(a['id'])}/{statement_b['id']}", json={"closing_balance": "1.00"}
    ).status_code == 404
    assert client.delete(f"{_statements(a['id'])}/{statement_b['id']}").status_code == 404

    # B still has it, unchanged.
    assert client.get(f"{_statements(b['id'])}/{statement_b['id']}").json()[
        "closing_balance"
    ] == "10500.00"


def test_unknown_company_lists_empty_but_cannot_create(client: TestClient) -> None:
    """Listings return an empty list, matching vendors and documents.

    Creating under an unknown company is refused, because there is nothing to
    attach a new record to.
    """
    ghost = uuid.uuid4()
    assert client.get(_accounts(ghost)).status_code == 200
    assert client.get(_accounts(ghost)).json() == []
    assert client.get(_statements(ghost)).json() == []

    account = client.post(
        _accounts(ghost), json={"bank_name": "HDFC Bank", "account_number": "1234"}
    )
    assert account.status_code == 404


# --- Bank transactions -----------------------------------------------------


def test_create_credit_transaction(client: TestClient) -> None:
    company = _company(client)
    account = _account(client, company["id"])
    statement = _statement(client, company["id"], account["id"]).json()

    response = _transaction(
        client,
        company["id"],
        statement["id"],
        credit_amount="2000.00",
        debit_amount="0.00",
        reference_number="NEFT001",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["transaction_type"] == "CREDIT"
    assert body["credit_amount"] == "2000.00"
    assert body["debit_amount"] == "0.00"
    assert body["reference_number"] == "NEFT001"
    assert body["bank_statement_id"] == statement["id"]
    assert body["company_id"] == company["id"]


def test_create_debit_transaction(client: TestClient) -> None:
    company = _company(client)
    account = _account(client, company["id"])
    statement = _statement(client, company["id"], account["id"]).json()

    response = _transaction(client, company["id"], statement["id"], debit_amount="1500.00", credit_amount="0.00")
    assert response.status_code == 201, response.text
    assert response.json()["transaction_type"] == "DEBIT"


def test_transaction_type_cannot_be_supplied_by_the_client(client: TestClient) -> None:
    """The direction is derived, and the field is not even accepted."""
    company = _company(client)
    account = _account(client, company["id"])
    statement = _statement(client, company["id"], account["id"]).json()

    response = _transaction(
        client,
        company["id"],
        statement["id"],
        debit_amount="750.25",
        credit_amount="0.00",
        transaction_type="CREDIT",
    )
    assert response.status_code == 422, response.text


def test_transaction_type_is_derived_from_the_amounts(client: TestClient) -> None:
    company = _company(client)
    account = _account(client, company["id"])
    statement = _statement(client, company["id"], account["id"]).json()

    debit = _transaction(
        client, company["id"], statement["id"], debit_amount="750.25", credit_amount="0.00"
    ).json()
    credit = _transaction(
        client,
        company["id"],
        statement["id"],
        transaction_date="2026-03-11",
        debit_amount="0.00",
        credit_amount="1.00",
    ).json()
    assert debit["transaction_type"] == "DEBIT"
    assert credit["transaction_type"] == "CREDIT"


def test_both_debit_and_credit_is_rejected(client: TestClient) -> None:
    company = _company(client)
    account = _account(client, company["id"])
    statement = _statement(client, company["id"], account["id"]).json()

    response = _transaction(
        client, company["id"], statement["id"], debit_amount="100.00", credit_amount="100.00"
    )
    assert response.status_code == 422, response.text
    assert "both a debit and a credit" in response.json()["detail"]
    assert client.get(f"{_statements(company['id'])}/{statement['id']}/transactions").json() == []


def test_zero_debit_and_credit_is_rejected(client: TestClient) -> None:
    company = _company(client)
    account = _account(client, company["id"])
    statement = _statement(client, company["id"], account["id"]).json()

    response = _transaction(
        client, company["id"], statement["id"], debit_amount="0.00", credit_amount="0.00"
    )
    assert response.status_code == 422, response.text
    assert "debit or a credit" in response.json()["detail"]


def test_omitting_both_amounts_is_rejected(client: TestClient) -> None:
    company = _company(client)
    account = _account(client, company["id"])
    statement = _statement(client, company["id"], account["id"]).json()

    response = client.post(
        f"{_statements(company['id'])}/{statement['id']}/transactions",
        json={"transaction_date": "2026-03-10"},
    )
    assert response.status_code == 422, response.text


def test_negative_amount_is_rejected(client: TestClient) -> None:
    company = _company(client)
    account = _account(client, company["id"])
    statement = _statement(client, company["id"], account["id"]).json()

    response = _transaction(
        client, company["id"], statement["id"], debit_amount="-500.00", credit_amount="0.00"
    )
    assert response.status_code == 422, response.text
    assert "negative" in response.json()["detail"]
    assert client.get(f"{_statements(company['id'])}/{statement['id']}/transactions").json() == []


def test_negative_balance_is_stored_with_a_review_flag(client: TestClient) -> None:
    """An overdraft is real data, so it is stored rather than blocked."""
    company = _company(client)
    account = _account(client, company["id"])
    statement = _statement(client, company["id"], account["id"]).json()

    response = _transaction(
        client, company["id"], statement["id"], debit_amount="2000.00", credit_amount="0.00", balance="-500.00"
    )
    assert response.status_code == 201, response.text
    assert response.json()["balance"] == "-500.00"


def test_list_and_get_transactions(client: TestClient) -> None:
    company = _company(client)
    account = _account(client, company["id"])
    statement = _statement(client, company["id"], account["id"]).json()

    late = _transaction(
        client, company["id"], statement["id"], transaction_date="2026-03-20", credit_amount="500.00"
    ).json()
    early = _transaction(
        client,
        company["id"],
        statement["id"],
        transaction_date="2026-03-05",
        debit_amount="100.00",
        credit_amount="0.00",
    ).json()

    listed = client.get(f"{_statements(company['id'])}/{statement['id']}/transactions")
    assert listed.status_code == 200
    # Ordered by date, not insertion order.
    assert [row["id"] for row in listed.json()] == [early["id"], late["id"]]

    fetched = client.get(
        f"{_statements(company['id'])}/{statement['id']}/transactions/{late['id']}"
    )
    assert fetched.status_code == 200
    assert fetched.json()["id"] == late["id"]


def test_list_transactions_filtered_by_date(client: TestClient) -> None:
    company = _company(client)
    account = _account(client, company["id"])
    statement = _statement(client, company["id"], account["id"]).json()
    _transaction(client, company["id"], statement["id"], transaction_date="2026-03-05")
    _transaction(client, company["id"], statement["id"], transaction_date="2026-03-20")

    filtered = client.get(
        f"{_statements(company['id'])}/{statement['id']}/transactions",
        params={"date_from": "2026-03-10"},
    )
    assert filtered.status_code == 200
    assert len(filtered.json()) == 1


def test_transaction_decimal_precision_is_exact(client: TestClient, db: Session) -> None:
    """Two decimal places survive the round trip with no float drift."""
    company = _company(client)
    account = _account(client, company["id"])
    statement = _statement(client, company["id"], account["id"]).json()

    response = _transaction(
        client,
        company["id"],
        statement["id"],
        credit_amount="1234567.89",
        balance="999999.99",
    )
    assert response.status_code == 201, response.text
    # Serialised as a string, never as a JSON number.
    assert response.json()["credit_amount"] == "1234567.89"
    assert '"credit_amount": "1234567.89"' in response.text.replace('"credit_amount":"1234567.89"', '"credit_amount": "1234567.89"')

    row = db.query(BankTransaction).filter(
        BankTransaction.id == uuid.UUID(response.json()["id"])
    ).one()
    assert isinstance(row.credit_amount, Decimal)
    assert row.credit_amount == Decimal("1234567.89")


def test_transaction_company_isolation(client: TestClient) -> None:
    a = _company(client, "Company A")
    b = _company(client, "Company B")
    account_b = _account(client, b["id"])
    statement_b = _statement(client, b["id"], account_b["id"]).json()
    transaction_b = _transaction(client, b["id"], statement_b["id"]).json()

    url = f"{_statements(a['id'])}/{statement_b['id']}/transactions/{transaction_b['id']}"
    assert client.get(url).status_code == 404
    assert client.get(f"{_statements(a['id'])}/{statement_b['id']}/transactions").status_code == 404
    assert client.post(
        f"{_statements(a['id'])}/{statement_b['id']}/transactions",
        json={"transaction_date": "2026-03-10", "credit_amount": "10.00"},
    ).status_code == 404


def test_transaction_must_belong_to_the_named_statement(client: TestClient) -> None:
    """A real transaction under someone else's statement is still 404."""
    a = _company(client, "Company A")
    b = _company(client, "Company B")
    account_a = _account(client, a["id"])
    statement_a1 = _statement(client, a["id"], account_a["id"]).json()
    statement_a2 = _statement(client, a["id"], account_a["id"]).json()
    transaction = _transaction(client, a["id"], statement_a1["id"]).json()

    response = client.get(
        f"{_statements(a['id'])}/{statement_a2['id']}/transactions/{transaction['id']}"
    )
    assert response.status_code == 404


def test_transactions_are_not_visible_from_another_companys_statement(client: TestClient) -> None:
    a = _company(client, "Company A")
    b = _company(client, "Company B")
    account_b = _account(client, b["id"])
    statement_b = _statement(client, b["id"], account_b["id"]).json()

    # Company A's statement lists zero rows, not company B's.
    response = client.get(f"{_statements(a['id'])}/{statement_b['id']}/transactions")
    assert response.status_code == 404


# --- Processing lifecycle --------------------------------------------------


def test_process_reports_extraction_not_configured(client: TestClient) -> None:
    company = _company(client)
    account = _account(client, company["id"])
    statement = _statement(client, company["id"], account["id"]).json()

    response = client.post(f"{_statements(company['id'])}/{statement['id']}/process")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["configured"] is False
    assert body["reason"] == EXTRACTION_NOT_CONFIGURED
    assert body["statement_id"] == statement["id"]


def test_process_does_not_fake_transactions_or_advance_status(client: TestClient) -> None:
    """No parser ran, so nothing may claim one did."""
    company = _company(client)
    account = _account(client, company["id"])
    statement = _statement(client, company["id"], account["id"]).json()

    client.post(f"{_statements(company['id'])}/{statement['id']}/process")
    client.post(f"{_statements(company['id'])}/{statement['id']}/process")

    after = client.get(f"{_statements(company['id'])}/{statement['id']}").json()
    assert after["status"] == "UPLOADED"
    assert client.get(f"{_statements(company['id'])}/{statement['id']}/transactions").json() == []


def test_process_is_company_scoped(client: TestClient) -> None:
    a = _company(client, "Company A")
    b = _company(client, "Company B")
    account_b = _account(client, b["id"])
    statement_b = _statement(client, b["id"], account_b["id"]).json()

    assert client.post(f"{_statements(a['id'])}/{statement_b['id']}/process").status_code == 404


# --- Deletion and document preservation ------------------------------------


def test_delete_statement_removes_transactions_but_keeps_document_and_account(
    client: TestClient,
) -> None:
    company = _company(client)
    account = _account(client, company["id"])
    document = _document(client, company["id"])
    statement = _statement(client, company["id"], account["id"], document_id=document["id"]).json()
    transaction = _transaction(client, company["id"], statement["id"]).json()

    response = client.delete(f"{_statements(company['id'])}/{statement['id']}")
    assert response.status_code == 204, response.text

    # Statement and its transactions are gone.
    assert client.get(f"{_statements(company['id'])}/{statement['id']}").status_code == 404
    assert client.get(
        f"{_statements(company['id'])}/{statement['id']}/transactions/{transaction['id']}"
    ).status_code == 404
    assert client.get(_statements(company["id"])).json() == []

    # The source document survives as the artifact of record.
    assert client.get(f"{_base(company['id'])}/documents/{document['id']}").status_code == 200
    # The bank account survives too.
    assert client.get(f"{_accounts(company['id'])}/{account['id']}").status_code == 200


def test_transaction_rows_are_actually_removed_from_the_table(client: TestClient, db: Session) -> None:
    """Cleanup is verified in the database, not just through the API."""
    company = _company(client)
    account = _account(client, company["id"])
    statement = _statement(client, company["id"], account["id"]).json()
    _transaction(client, company["id"], statement["id"])
    _transaction(client, company["id"], statement["id"], transaction_date="2026-03-12")

    assert db.query(BankTransaction).count() == 2

    client.delete(f"{_statements(company['id'])}/{statement['id']}")
    db.expire_all()
    assert db.query(BankTransaction).count() == 0


# --- Validation service unit tests ----------------------------------------


def test_reconcile_balances_accepts_a_matching_statement() -> None:
    result = reconcile_balances(
        opening_balance=Decimal("10000.00"),
        closing_balance=Decimal("10500.00"),
        debits=[Decimal("1500.00")],
        credits=[Decimal("2000.00")],
    )
    assert result.is_valid


def test_reconcile_balances_reports_a_mismatch_without_changing_anything() -> None:
    """The documented example: 10000 +2000 -1500 = 10500, not 11000."""
    result = reconcile_balances(
        opening_balance=Decimal("10000.00"),
        closing_balance=Decimal("11000.00"),
        debits=[Decimal("1500.00")],
        credits=[Decimal("2000.00")],
    )
    assert not result.is_valid
    assert "10500.00" in result.issues[0]
    assert "11000.00" in result.issues[0]


def test_reconcile_balances_tolerates_half_a_paisa() -> None:
    result = reconcile_balances(
        opening_balance=Decimal("100.00"),
        closing_balance=Decimal("100.01"),
        debits=[],
        credits=[Decimal("0.00")],
    )
    assert result.is_valid


def test_reconcile_balances_skipped_when_balances_unknown() -> None:
    assert reconcile_balances(
        opening_balance=None, closing_balance=Decimal("1.00"), debits=[], credits=[]
    ).is_valid


def test_unreconciled_statement_is_reported_but_data_is_untouched(
    client: TestClient, db: Session
) -> None:
    company = _company(client)
    account = _account(client, company["id"])
    statement = _statement(
        client,
        company["id"],
        account["id"],
        opening_balance="10000.00",
        closing_balance="11000.00",
    ).json()
    _transaction(client, company["id"], statement["id"], credit_amount="2000.00")
    _transaction(
        client,
        company["id"],
        statement["id"],
        transaction_date="2026-03-12",
        debit_amount="1500.00",
        credit_amount="0.00",
    )

    response = client.post(f"{_statements(company['id'])}/{statement['id']}/process")
    assert response.status_code == 200
    assert response.json()["issues"], "mismatch should be surfaced"

    db.expire_all()
    row = db.get(BankStatement, uuid.UUID(statement["id"]))
    assert row.closing_balance == Decimal("11000.00")  # not "corrected" to 10500


def test_validate_statement_flags_negative_balance_for_review() -> None:
    result = validate_statement(
        statement_start_date=date(2026, 3, 1),
        statement_end_date=date(2026, 3, 31),
        opening_balance=Decimal("-250.00"),
        closing_balance=Decimal("100.00"),
    )
    assert not result.is_valid
    assert result.fatal == frozenset()  # a review flag, not a rejection


def test_validate_statement_rejects_inverted_period_fatally() -> None:
    result = validate_statement(
        statement_start_date=date(2026, 3, 31), statement_end_date=date(2026, 3, 1)
    )
    assert result.fatal


def test_validate_transaction_flags_value_date_before_transaction_date() -> None:
    result = validate_transaction(
        transaction_date=date(2026, 3, 10),
        value_date=date(2026, 3, 9),
        debit_amount=Decimal("10.00"),
        credit_amount=Decimal("0"),
    )
    assert not result.is_valid
    assert result.fatal == frozenset()


@pytest.mark.parametrize(
    ("debit", "credit"),
    [
        ("0", "0"),
        ("-5.00", "0"),
        ("0", "-5.00"),
        ("10.00", "10.00"),
    ],
)
def test_structurally_invalid_transactions_are_fatal(debit: str, credit: str) -> None:
    result = validate_transaction(
        transaction_date=date(2026, 3, 10),
        debit_amount=Decimal(debit),
        credit_amount=Decimal(credit),
    )
    assert result.fatal


def test_reject_if_fatal_raises() -> None:
    result = validate_transaction(
        transaction_date=date(2026, 3, 10),
        debit_amount=Decimal("0"),
        credit_amount=Decimal("0"),
    )
    with pytest.raises(BankValidationError):
        reject_if_fatal(result)


def test_legal_status_transitions_are_enforced() -> None:
    assert_transition_allowed("UPLOADED", "PROCESSING")
    assert_transition_allowed("PROCESSING", "PROCESSED")
    with pytest.raises(BankTransitionError):
        assert_transition_allowed("UPLOADED", "PROCESSED")
    with pytest.raises(BankTransitionError):
        assert_transition_allowed("PROCESSED", "UPLOADED")