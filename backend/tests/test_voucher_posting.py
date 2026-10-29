"""Direct posting of an approved voucher into TallyPrime.

PAL's only write into Tally, so the gates matter more than the transport:
approval cannot be bypassed, a double click cannot post twice, and a refusal
leaves the voucher retryable and never POSTED.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.integrations.tally import TallyClient, TallyService
from app.models.voucher import Voucher, VoucherStatus
from app.services.voucher_posting import post_voucher_to_tally
from app.services.voucher_validation import (
    VoucherTransitionError,
    VoucherValidationError,
)

COMPANIES_URL = "/api/v1/companies"

# Verbatim shape from a live TallyPrime instance: a bare <RESPONSE> root, and
# a refusal reported in EXCEPTIONS with ERRORS still 0.
ACCEPTED = """<RESPONSE>
 <CREATED>1</CREATED>
 <ALTERED>0</ALTERED>
 <DELETED>0</DELETED>
 <LASTVCHID>4821</LASTVCHID>
 <LASTMID>0</LASTMID>
 <COMBINED>0</COMBINED>
 <IGNORED>0</IGNORED>
 <ERRORS>0</ERRORS>
 <CANCELLED>0</CANCELLED>
 <EXCEPTIONS>0</EXCEPTIONS>
</RESPONSE>"""

REJECTED = """<RESPONSE>
 <LINEERROR>Ledger &apos;Freight Inward&apos; does not exist!</LINEERROR>
 <CREATED>0</CREATED>
 <ALTERED>0</ALTERED>
 <DELETED>0</DELETED>
 <LASTVCHID>0</LASTVCHID>
 <LASTMID>0</LASTMID>
 <COMBINED>0</COMBINED>
 <IGNORED>0</IGNORED>
 <ERRORS>0</ERRORS>
 <CANCELLED>0</CANCELLED>
 <EXCEPTIONS>1</EXCEPTIONS>
</RESPONSE>"""


class _Recorder:
    """Captures every payload Tally would receive."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.payloads: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.payloads.append(request.content.decode("utf-8"))
        return httpx.Response(200, text=self.response)


def _service(handler) -> TallyService:
    return TallyService(
        TallyClient(
            base_url="http://tally.test:9000",
            transport=httpx.MockTransport(handler),
        )
    )


def _base(company_id: str) -> str:
    return f"{COMPANIES_URL}/{company_id}"


def _approved_voucher(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    """A balanced two-line voucher taken all the way to APPROVED."""
    company = client.post(COMPANIES_URL, json={"name": "PAL Test Company"})
    assert company.status_code == 201, company.text
    company = company.json()
    expense = client.post(
        f"{_base(company['id'])}/ledgers", json={"name": "Freight Inward"}
    ).json()
    party = client.post(
        f"{_base(company['id'])}/ledgers", json={"name": "Deccan Freight"}
    ).json()
    created = client.post(
        f"{_base(company['id'])}/vouchers",
        json={
            "voucher_date": "2026-03-15",
            "voucher_type": "PURCHASE",
            "narration": "Freight for March",
            "lines": [
                {"ledger_id": expense["id"], "side": "Dr", "amount": "1000.00"},
                {"ledger_id": party["id"], "side": "Cr", "amount": "1000.00"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    voucher = created.json()
    client.post(f"{_base(company['id'])}/vouchers/{voucher['id']}/submit", json={})
    approved = client.post(
        f"{_base(company['id'])}/vouchers/{voucher['id']}/approve", json={}
    )
    assert approved.status_code == 200, approved.text
    return company, approved.json()


def test_voucher_awaiting_approval_cannot_be_posted(client: TestClient) -> None:
    """Submitted is not approved: approval can never be bypassed."""
    company = client.post(COMPANIES_URL, json={"name": "PAL Test Company"}).json()
    ledger = client.post(
        f"{_base(company['id'])}/ledgers", json={"name": "Freight Inward"}
    ).json()
    voucher = client.post(
        f"{_base(company['id'])}/vouchers",
        json={
            "voucher_date": "2026-03-15",
            "voucher_type": "PURCHASE",
            "lines": [
                {"ledger_id": ledger["id"], "side": "Dr", "amount": "10.00"},
                {"ledger_id": ledger["id"], "side": "Cr", "amount": "10.00"},
            ],
        },
    ).json()
    submitted = client.post(
        f"{_base(company['id'])}/vouchers/{voucher['id']}/submit", json={}
    ).json()
    assert submitted["status"] == "PENDING_APPROVAL"

    response = client.post(
        f"{_base(company['id'])}/vouchers/{voucher['id']}/post-to-tally", json={}
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "APPROVAL_REQUIRED"


def test_draft_voucher_cannot_be_posted(client: TestClient, db: Session) -> None:
    company = client.post(COMPANIES_URL, json={"name": "PAL Test Company"}).json()
    ledger = client.post(
        f"{_base(company['id'])}/ledgers", json={"name": "Freight Inward"}
    ).json()
    voucher = client.post(
        f"{_base(company['id'])}/vouchers",
        json={
            "voucher_date": "2026-03-15",
            "voucher_type": "PURCHASE",
            "lines": [
                {"ledger_id": ledger["id"], "side": "Dr", "amount": "10.00"},
                {"ledger_id": ledger["id"], "side": "Cr", "amount": "10.00"},
            ],
        },
    ).json()

    response = client.post(
        f"{_base(company['id'])}/vouchers/{voucher['id']}/post-to-tally", json={}
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "APPROVAL_REQUIRED"


def test_successful_post_sets_posted_and_stores_the_reference(
    client: TestClient, db: Session
) -> None:
    company, voucher = _approved_voucher(client)
    recorder = _Recorder(ACCEPTED)

    result = post_voucher_to_tally(
        db,
        voucher["company_id"],
        voucher["id"],
        actor="tester",
        service=_service(recorder),
    )

    assert result.success is True
    assert result.status == "POSTED"
    assert result.tally_reference == "4821"
    assert result.already_posted is False

    row = client.get(f"{_base(company['id'])}/vouchers/{voucher['id']}").json()
    assert row["status"] == "POSTED"
    assert row["posting_reference"] == "4821"
    assert row["posted_at"] is not None

    # One request, carrying the import report and PAL's id as REMOTEID.
    assert len(recorder.payloads) == 1
    sent = recorder.payloads[0]
    assert "<TALLYREQUEST>Import Data</TALLYREQUEST>" in sent
    assert "<REPORTNAME>Vouchers</REPORTNAME>" in sent
    assert f'REMOTEID="{voucher["id"]}"' in sent
    # A debit is negative with ISDEEMEDPOSITIVE Yes, a credit the mirror.
    assert "<AMOUNT>-1000.00</AMOUNT>" in sent
    assert "<AMOUNT>1000.00</AMOUNT>" in sent


def test_posting_is_audited_with_the_tally_reference(
    client: TestClient, db: Session
) -> None:
    company, voucher = _approved_voucher(client)
    post_voucher_to_tally(
        db,
        voucher["company_id"],
        voucher["id"],
        actor="tester",
        service=_service(_Recorder(ACCEPTED)),
    )

    events = client.get(
        f"{_base(company['id'])}/vouchers/{voucher['id']}/audit"
    ).json()
    types = [event["event_type"] for event in events]
    assert "VOUCHER_POSTING_STARTED" in types
    assert "VOUCHER_POSTED_TO_TALLY" in types
    posted = next(e for e in events if e["event_type"] == "VOUCHER_POSTED_TO_TALLY")
    assert posted["new_status"] == "POSTED"
    assert posted["event_metadata"]["tally_reference"] == "4821"
    assert posted["event_metadata"]["remote_id"] == voucher["id"]


def test_tally_rejection_leaves_the_voucher_retryable(
    client: TestClient, db: Session
) -> None:
    company, voucher = _approved_voucher(client)

    result = post_voucher_to_tally(
        db,
        voucher["company_id"],
        voucher["id"],
        actor="tester",
        service=_service(_Recorder(REJECTED)),
    )

    assert result.success is False
    assert result.status == "POSTING_FAILED"
    assert result.tally_reference is None
    assert "does not exist" in result.message

    row = client.get(f"{_base(company['id'])}/vouchers/{voucher['id']}").json()
    assert row["status"] == "POSTING_FAILED"
    assert row["posting_reference"] is None
    assert "does not exist" in row["posting_error"]

    # The retry succeeds and lands POSTED.
    retried = post_voucher_to_tally(
        db,
        voucher["company_id"],
        voucher["id"],
        actor="tester",
        service=_service(_Recorder(ACCEPTED)),
    )
    assert retried.success is True
    assert retried.status == "POSTED"


def test_connection_failure_does_not_mark_posted(
    client: TestClient, db: Session
) -> None:
    company, voucher = _approved_voucher(client)

    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = post_voucher_to_tally(
        db,
        voucher["company_id"],
        voucher["id"],
        actor="tester",
        service=_service(explode),
    )

    assert result.success is False
    assert result.status == "POSTING_FAILED"
    row = client.get(f"{_base(company['id'])}/vouchers/{voucher['id']}").json()
    assert row["status"] == "POSTING_FAILED"
    assert row["posted_at"] is None


def test_second_click_does_not_post_again(client: TestClient, db: Session) -> None:
    """Idempotency: PAL's own POSTED state answers without touching Tally."""
    company, voucher = _approved_voucher(client)
    first = _Recorder(ACCEPTED)
    post_voucher_to_tally(
        db, voucher["company_id"], voucher["id"], service=_service(first)
    )

    second = _Recorder(ACCEPTED)
    again = post_voucher_to_tally(
        db, voucher["company_id"], voucher["id"], service=_service(second)
    )

    assert again.success is True
    assert again.already_posted is True
    assert again.tally_reference == "4821"
    assert len(first.payloads) == 1
    # Tally was never contacted the second time.
    assert second.payloads == []


def test_posted_voucher_endpoint_is_idempotent(client: TestClient) -> None:
    company, voucher = _approved_voucher(client)
    url = f"{_base(company['id'])}/vouchers/{voucher['id']}/post-to-tally"

    import app.services.voucher_posting as posting

    recorder = _Recorder(ACCEPTED)
    original = posting.get_tally_service
    posting.get_tally_service = lambda: _service(recorder)  # type: ignore[assignment]
    try:
        first = client.post(url, json={}).json()
        second = client.post(url, json={}).json()
    finally:
        posting.get_tally_service = original  # type: ignore[assignment]

    assert first["success"] and first["already_posted"] is False
    assert second["success"] and second["already_posted"] is True
    assert len(recorder.payloads) == 1


def test_unbalanced_voucher_is_never_sent(client: TestClient, db: Session) -> None:
    """Approval could predate an edit; an unbalanced voucher must not post."""
    company, voucher = _approved_voucher(client)
    row = db.get(Voucher, voucher["id"])
    row.lines[0].amount = row.lines[0].amount + 1
    db.commit()
    recorder = _Recorder(ACCEPTED)

    with pytest.raises(VoucherValidationError):
        post_voucher_to_tally(
            db,
            voucher["company_id"],
            voucher["id"],
            service=_service(recorder),
        )

    assert recorder.payloads == []
    db.refresh(row)
    assert row.status is VoucherStatus.APPROVED


def test_exceptions_without_errors_is_still_a_refusal(db: Session) -> None:
    """Tally reports a rejected voucher as EXCEPTIONS=1 with ERRORS=0."""
    from app.integrations.tally import parse_import_result

    result = parse_import_result(REJECTED)
    assert result.errors == 0
    assert result.exceptions == 1
    assert result.accepted is False
    assert "does not exist" in (result.message or "")
    # LASTVCHID of 0 means nothing was stored, so it is not a reference.
    assert result.last_voucher_id is None


def test_response_root_is_parsed_not_rejected(db: Session) -> None:
    """A bare <RESPONSE> root must not read as an unexpected response."""
    from app.integrations.tally import parse_import_result

    result = parse_import_result(ACCEPTED)
    assert result.accepted is True
    assert result.created == 1
    assert result.last_voucher_id == "4821"


def test_posting_never_alters_amounts_or_ledgers(
    client: TestClient, db: Session
) -> None:
    company, voucher = _approved_voucher(client)
    before = client.get(
        f"{_base(company['id'])}/vouchers/{voucher['id']}"
    ).json()["lines"]

    post_voucher_to_tally(
        db,
        voucher["company_id"],
        voucher["id"],
        service=_service(_Recorder(ACCEPTED)),
    )

    after = client.get(
        f"{_base(company['id'])}/vouchers/{voucher['id']}"
    ).json()["lines"]
    assert [(l["ledger_name"], l["side"], l["amount"]) for l in before] == [
        (l["ledger_name"], l["side"], l["amount"]) for l in after
    ]
