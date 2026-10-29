"""Tests for TallyPrime integration: XML POC, connectivity, and ledger sync.

The normal suite never contacts a live TallyPrime. HTTP is mocked with
``httpx.MockTransport``; ``test_tally_live.py`` is the separate opt-in smoke
test.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.api.v1.tally.router as tally_router
import app.services.tally_sync as tally_sync_module
from app.core.config import get_settings
from app.integrations.tally import (
    TallyClient,
    TallyConfigurationError,
    TallyConnectionError,
    TallyHTTPError,
    TallyResponseError,
    TallyService,
    TallyTimeoutError,
    build_company_collection_request,
    build_ledger_collection_request,
    parse_companies,
    parse_ledgers,
)
from app.main import app
from app.models.company import Company
from app.models.ledger import Ledger, LedgerSource

api_client = TestClient(app)

COMPANY_XML = """<ENVELOPE>
 <HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER>
 <BODY>
  <DESC><CMPINFO><COMPANY>0</COMPANY><LEDGER>0</LEDGER></CMPINFO></DESC>
  <DATA>
   <COLLECTION>
    <COMPANY NAME="PAL Test Company" RESERVEDNAME="">
     <NAME TYPE="String">PAL Test Company</NAME>
    </COMPANY>
    <COMPANY NAME="Second Co" RESERVEDNAME="">
     <NAME TYPE="String">Second Co</NAME>
    </COMPANY>
   </COLLECTION>
  </DATA>
 </BODY>
</ENVELOPE>"""

EMPTY_COMPANY_XML = """<ENVELOPE>
 <HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER>
 <BODY><DATA><COLLECTION></COLLECTION></DATA></BODY>
</ENVELOPE>"""

LEDGER_XML = """<ENVELOPE>
 <HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER>
 <BODY>
  <DATA>
   <COLLECTION ISMSTDEPTYPE="Yes" MSTDEPTYPE="8">
    <LEDGER NAME="Cash" RESERVEDNAME="">
     <PARENT TYPE="String">Cash-in-Hand</PARENT>
     <LANGUAGENAME.LIST>
      <NAME.LIST TYPE="String"><NAME>Cash</NAME></NAME.LIST>
     </LANGUAGENAME.LIST>
    </LEDGER>
    <LEDGER NAME="Profit &amp; Loss A/c" RESERVEDNAME="">
     <PARENT TYPE="String">&#4; Primary</PARENT>
     <LANGUAGENAME.LIST>
      <NAME.LIST TYPE="String"><NAME>Profit &amp; Loss A/c</NAME></NAME.LIST>
     </LANGUAGENAME.LIST>
    </LEDGER>
   </COLLECTION>
  </DATA>
 </BODY>
</ENVELOPE>"""

TALLY_ERROR_XML = """<ENVELOPE>
 <HEADER><VERSION>1</VERSION><STATUS>0</STATUS></HEADER>
 <BODY><DATA><ERROR>secret internal Tally detail</ERROR></DATA></BODY>
</ENVELOPE>"""


def _client(handler) -> TallyClient:
    return TallyClient(
        base_url="http://tally.test:9000",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )


def _install_service(monkeypatch: pytest.MonkeyPatch, handler) -> TallyService:
    service = TallyService(_client(handler))
    monkeypatch.setattr(tally_router, "get_tally_service", lambda: service)
    return service


class TestXmlBuilders:
    def test_company_request_is_a_read_only_collection(self):
        payload = build_company_collection_request()
        assert "<TALLYREQUEST>Export</TALLYREQUEST>" in payload
        assert "<TYPE>Collection</TYPE>" in payload
        assert "<ID>List of Companies</ID>" in payload
        assert "<TYPE>Company</TYPE>" in payload
        assert "SVCURRENTCOMPANY" not in payload

    def test_ledger_request_escapes_company_name(self):
        payload = build_ledger_collection_request("PAL & Co")
        assert "<SVCURRENTCOMPANY>PAL &amp; Co</SVCURRENTCOMPANY>" in payload
        assert "<TYPE>Ledger</TYPE>" in payload
        assert "<FETCH>Name, Parent</FETCH>" in payload

    def test_ledger_request_can_omit_company(self):
        assert "SVCURRENTCOMPANY" not in build_ledger_collection_request()


class TestCompanyParsing:
    def test_parses_company_names_and_ignores_cmpinfo_counters(self):
        companies = parse_companies(COMPANY_XML)
        assert [company.name for company in companies] == [
            "PAL Test Company",
            "Second Co",
        ]

    def test_empty_company_collection_returns_empty_list(self):
        assert parse_companies(EMPTY_COMPANY_XML) == []

    def test_malformed_xml_raises_safe_error(self):
        with pytest.raises(TallyResponseError) as exc_info:
            parse_companies("<ENVELOPE><BODY>")
        assert str(exc_info.value) == "TallyPrime returned malformed XML."

    def test_tally_error_status_raises_generic_error(self):
        with pytest.raises(TallyResponseError) as exc_info:
            parse_companies(TALLY_ERROR_XML)
        assert str(exc_info.value) == "TallyPrime reported an error."
        assert exc_info.value.detail == "secret internal Tally detail"

    def test_unexpected_root_raises_safe_error(self):
        with pytest.raises(TallyResponseError) as exc_info:
            parse_companies("<RESPONSE>TallyPrime Server is Running</RESPONSE>")
        assert str(exc_info.value) == "TallyPrime returned an unexpected response."


class TestLedgerParsing:
    def test_parses_ledger_names_and_parents(self):
        result = parse_ledgers(LEDGER_XML, company="PAL Test Company")
        assert result.company == "PAL Test Company"
        assert [(ledger.name, ledger.parent) for ledger in result.ledgers] == [
            ("Cash", "Cash-in-Hand"),
            ("Profit & Loss A/c", "Primary"),
        ]

    def test_duplicate_ledgers_are_collapsed(self):
        duplicate = LEDGER_XML.replace(
            "</COLLECTION>",
            '<LEDGER NAME="Cash"><PARENT>Cash-in-Hand</PARENT></LEDGER>'
            "</COLLECTION>",
        )
        result = parse_ledgers(duplicate)
        assert len(result.ledgers) == 2

    def test_empty_ledger_collection_returns_empty_list(self):
        result = parse_ledgers(EMPTY_COMPANY_XML)
        assert result.ledgers == []
        assert result.company is None


class TestTallyClient:
    def test_posts_xml_to_configured_endpoint(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, text=COMPANY_XML)

        with _client(handler) as client:
            body = client.post_xml(build_company_collection_request())

        assert body == COMPANY_XML
        assert len(seen) == 1
        assert seen[0].method == "POST"
        assert str(seen[0].url) == "http://tally.test:9000/"
        assert seen[0].headers["content-type"] == "text/xml; charset=utf-8"
        assert "List of Companies" in seen[0].content.decode("utf-8")

    def test_connection_error_is_converted(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        with _client(handler) as client:
            with pytest.raises(TallyConnectionError) as exc_info:
                client.post_xml(build_company_collection_request())
        assert str(exc_info.value) == "Unable to connect to TallyPrime."

    def test_timeout_is_converted(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        with _client(handler) as client:
            with pytest.raises(TallyTimeoutError) as exc_info:
                client.post_xml(build_company_collection_request())
        assert str(exc_info.value) == "TallyPrime did not respond before the timeout."

    def test_http_error_is_converted(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="not safe to expose")

        with _client(handler) as client:
            with pytest.raises(TallyHTTPError) as exc_info:
                client.post_xml(build_company_collection_request())
        assert str(exc_info.value) == "TallyPrime returned HTTP 500."
        assert "not safe to expose" not in str(exc_info.value)

    def test_empty_response_is_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"")

        with _client(handler) as client:
            with pytest.raises(TallyResponseError):
                client.post_xml(build_company_collection_request())

    def test_utf16_response_is_decoded(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=COMPANY_XML.encode("utf-16"))

        with _client(handler) as client:
            body = client.post_xml(build_company_collection_request())
        assert "PAL Test Company" in body
        assert [company.name for company in parse_companies(body)] == [
            "PAL Test Company",
            "Second Co",
        ]

    @pytest.mark.parametrize(
        "base_url",
        [
            "file:///etc/passwd",
            "http://user:secret@tally.test:9000",
            "http://tally.test:9000?target=elsewhere",
            "http://tally.test:9000/tally",
            "http://tally.test:notaport",
        ],
    )
    def test_invalid_base_url_is_rejected(self, base_url):
        with pytest.raises(TallyConfigurationError):
            TallyClient(base_url=base_url)

    @pytest.mark.parametrize("timeout", [0, -1, 61])
    def test_timeout_must_be_bounded(self, timeout):
        with pytest.raises(TallyConfigurationError):
            TallyClient(
                base_url="http://tally.test:9000",
                timeout_seconds=timeout,
            )

    def test_empty_payload_is_rejected(self):
        with _client(lambda request: httpx.Response(200, text=COMPANY_XML)) as client:
            with pytest.raises(TallyConfigurationError):
                client.post_xml("   ")


class TestTallyService:
    def test_check_connection_true_when_companies_respond(self):
        with TallyService(
            _client(lambda request: httpx.Response(200, text=COMPANY_XML))
        ) as service:
            assert service.check_connection() is True

    def test_check_connection_false_on_connection_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        with TallyService(_client(handler)) as service:
            assert service.check_connection() is False

    def test_get_ledgers_uses_first_company_when_none_supplied(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = request.content.decode("utf-8")
            seen.append(body)
            if "List of Companies" in body:
                return httpx.Response(200, text=COMPANY_XML)
            return httpx.Response(200, text=LEDGER_XML)

        with TallyService(_client(handler)) as service:
            result = service.get_ledgers()

        assert result.company == "PAL Test Company"
        assert len(result.ledgers) == 2
        assert len(seen) == 2
        assert "<SVCURRENTCOMPANY>PAL Test Company</SVCURRENTCOMPANY>" in seen[1]

    def test_get_ledgers_returns_message_when_no_company(self):
        with TallyService(
            _client(lambda request: httpx.Response(200, text=EMPTY_COMPANY_XML))
        ) as service:
            result = service.get_ledgers()
        assert result.company is None
        assert result.ledgers == []
        assert result.message == "No company is loaded in TallyPrime."

    def test_get_ledgers_with_explicit_company_skips_company_lookup(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.content.decode("utf-8"))
            return httpx.Response(200, text=LEDGER_XML)

        with TallyService(_client(handler)) as service:
            result = service.get_ledgers("PAL Test Company")

        assert result.company == "PAL Test Company"
        assert len(seen) == 1
        assert "<SVCURRENTCOMPANY>PAL Test Company</SVCURRENTCOMPANY>" in seen[0]

    def test_blank_explicit_company_is_rejected(self):
        with TallyService(
            _client(lambda request: httpx.Response(200, text=LEDGER_XML))
        ) as service:
            with pytest.raises(TallyConfigurationError):
                service.get_ledgers("   ")


class TestTallyEndpoints:
    def test_status_reports_connected(self, monkeypatch):
        monkeypatch.setenv("TALLY_BASE_URL", "http://tally.test:9000")
        get_settings.cache_clear()
        _install_service(
            monkeypatch,
            lambda request: httpx.Response(200, text=COMPANY_XML),
        )

        response = api_client.get("/api/v1/tally/status")

        assert response.status_code == 200
        assert response.json() == {
            "connected": True,
            "url": "http://tally.test:9000",
            "message": "TallyPrime is reachable",
        }

    def test_status_reports_disconnected_without_leaking_details(self, monkeypatch):
        monkeypatch.setenv("TALLY_BASE_URL", "http://tally.test:9000")
        get_settings.cache_clear()

        class BrokenService:
            def check_connection(self):
                raise TallyConnectionError("secret socket detail")

            def close(self):
                pass

        monkeypatch.setattr(
            tally_router, "get_tally_service", lambda: BrokenService()
        )

        response = api_client.get("/api/v1/tally/status")

        assert response.status_code == 200
        assert response.json() == {
            "connected": False,
            "url": "http://tally.test:9000",
            "message": "Unable to connect to TallyPrime",
        }
        assert "secret" not in response.text

    def test_status_does_not_expose_credentials_from_a_bad_url(self, monkeypatch):
        monkeypatch.setenv(
            "TALLY_BASE_URL", "http://user:secret@tally.test:9000"
        )
        get_settings.cache_clear()

        response = api_client.get("/api/v1/tally/status")

        assert response.status_code == 200
        assert response.json() == {
            "connected": False,
            "url": "http://tally.test:9000",
            "message": "Unable to connect to TallyPrime",
        }
        assert "secret" not in response.text

    def test_companies_returns_all_companies(self, monkeypatch):
        _install_service(
            monkeypatch,
            lambda request: httpx.Response(200, text=COMPANY_XML),
        )

        response = api_client.get("/api/v1/tally/companies")

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "companies": [
                {"name": "PAL Test Company"},
                {"name": "Second Co"},
            ],
            "message": None,
        }

    def test_companies_empty_response_has_meaningful_message(self, monkeypatch):
        _install_service(
            monkeypatch,
            lambda request: httpx.Response(200, text=EMPTY_COMPANY_XML),
        )

        response = api_client.get("/api/v1/tally/companies")

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "companies": [],
            "message": "No company is loaded in TallyPrime.",
        }

    def test_companies_connection_error_maps_to_502(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        _install_service(monkeypatch, handler)

        response = api_client.get("/api/v1/tally/companies")

        assert response.status_code == 502
        assert response.json() == {"detail": "Unable to connect to TallyPrime."}

    def test_companies_timeout_maps_to_504(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        _install_service(monkeypatch, handler)

        response = api_client.get("/api/v1/tally/companies")

        assert response.status_code == 504
        assert response.json() == {
            "detail": "TallyPrime did not respond before the timeout."
        }

    def test_companies_malformed_xml_maps_to_502(self, monkeypatch):
        _install_service(
            monkeypatch,
            lambda request: httpx.Response(200, text="<ENVELOPE><BODY>"),
        )

        response = api_client.get("/api/v1/tally/companies")

        assert response.status_code == 502
        assert response.json() == {"detail": "TallyPrime returned malformed XML."}

    def test_companies_tally_error_does_not_expose_internal_detail(self, monkeypatch):
        _install_service(
            monkeypatch,
            lambda request: httpx.Response(200, text=TALLY_ERROR_XML),
        )

        response = api_client.get("/api/v1/tally/companies")

        assert response.status_code == 502
        assert response.json() == {"detail": "TallyPrime reported an error."}
        assert "secret internal Tally detail" not in response.text

    def test_ledgers_returns_company_and_masters(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            if "List of Companies" in request.content.decode("utf-8"):
                return httpx.Response(200, text=COMPANY_XML)
            return httpx.Response(200, text=LEDGER_XML)

        _install_service(monkeypatch, handler)

        response = api_client.get("/api/v1/tally/ledgers")

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "company": "PAL Test Company",
            "ledgers": [
                {"name": "Cash", "parent": "Cash-in-Hand"},
                {"name": "Profit & Loss A/c", "parent": "Primary"},
            ],
            "message": None,
        }

    def test_ledgers_without_company_returns_empty_list_and_message(self, monkeypatch):
        _install_service(
            monkeypatch,
            lambda request: httpx.Response(200, text=EMPTY_COMPANY_XML),
        )

        response = api_client.get("/api/v1/tally/ledgers")

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "company": None,
            "ledgers": [],
            "message": "No company is loaded in TallyPrime.",
        }

    def test_ledgers_accepts_an_explicit_company(self, monkeypatch):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.content.decode("utf-8"))
            return httpx.Response(200, text=LEDGER_XML)

        _install_service(monkeypatch, handler)

        response = api_client.get(
            "/api/v1/tally/ledgers",
            params={"company": "PAL Test Company"},
        )

        assert response.status_code == 200
        assert response.json()["company"] == "PAL Test Company"
        assert len(seen) == 1
        assert "<SVCURRENTCOMPANY>PAL Test Company</SVCURRENTCOMPANY>" in seen[0]

    def test_ledgers_rejects_blank_company(self, monkeypatch):
        _install_service(
            monkeypatch,
            lambda request: httpx.Response(200, text=LEDGER_XML),
        )

        response = api_client.get(
            "/api/v1/tally/ledgers",
            params={"company": "   "},
        )

        assert response.status_code == 422
        assert response.json() == {"detail": "Company name must not be blank."}


# ---------------------------------------------------------------------------
# Sync service tests — use the real test DB via the db fixture from conftest.
# ---------------------------------------------------------------------------

def _make_company(db: Session) -> Company:
    company = Company(name="Test Co", legal_name="Test Co Ltd")
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def _make_service(handler) -> TallyService:
    return TallyService(
        TallyClient(
            base_url="http://tally.test:9000",
            timeout_seconds=5,
            transport=httpx.MockTransport(handler),
        )
    )


class TestTallySyncService:
    def test_first_sync_creates_ledgers(self, db: Session):
        from app.services.tally_sync import sync_tally_ledgers

        company = _make_company(db)
        svc = _make_service(lambda r: httpx.Response(200, text=LEDGER_XML))

        log = sync_tally_ledgers(
            db, company.id, "PAL Test Company", actor="test", service=svc
        )

        assert log.error_message is None
        assert log.ledgers_fetched == 2
        assert log.ledgers_created == 2
        assert log.ledgers_updated == 0
        assert log.ledgers_unchanged == 0
        ledgers = db.query(Ledger).filter(Ledger.company_id == company.id).all()
        assert len(ledgers) == 2
        names = {l.name for l in ledgers}
        assert names == {"Cash", "Profit & Loss A/c"}
        cash = next(l for l in ledgers if l.name == "Cash")
        assert cash.parent_group == "Cash-in-Hand"
        assert cash.source == LedgerSource.TALLY

    def test_repeated_sync_creates_no_duplicates(self, db: Session):
        from app.services.tally_sync import sync_tally_ledgers

        company = _make_company(db)
        svc1 = _make_service(lambda r: httpx.Response(200, text=LEDGER_XML))
        svc2 = _make_service(lambda r: httpx.Response(200, text=LEDGER_XML))

        sync_tally_ledgers(db, company.id, "PAL Test Company", service=svc1)
        log2 = sync_tally_ledgers(db, company.id, "PAL Test Company", service=svc2)

        assert log2.ledgers_created == 0
        assert log2.ledgers_unchanged == 2
        assert db.query(Ledger).filter(Ledger.company_id == company.id).count() == 2

    def test_changed_parent_is_updated(self, db: Session):
        from app.services.tally_sync import sync_tally_ledgers

        company = _make_company(db)
        original = _make_service(lambda r: httpx.Response(200, text=LEDGER_XML))
        sync_tally_ledgers(db, company.id, "PAL Test Company", service=original)

        changed_xml = LEDGER_XML.replace("Cash-in-Hand", "Bank Accounts")
        updated = _make_service(lambda r: httpx.Response(200, text=changed_xml))
        log = sync_tally_ledgers(db, company.id, "PAL Test Company", service=updated)

        assert log.ledgers_updated == 1
        cash = db.query(Ledger).filter(
            Ledger.company_id == company.id, Ledger.name == "Cash"
        ).one()
        assert cash.parent_group == "Bank Accounts"

    def test_company_isolation(self, db: Session):
        from app.services.tally_sync import sync_tally_ledgers

        co_a = _make_company(db)
        co_b = Company(name="Other Co")
        db.add(co_b)
        db.commit()
        db.refresh(co_b)

        svc = _make_service(lambda r: httpx.Response(200, text=LEDGER_XML))
        sync_tally_ledgers(db, co_a.id, "PAL Test Company", service=svc)

        assert db.query(Ledger).filter(Ledger.company_id == co_b.id).count() == 0

    def test_tally_failure_records_error_without_creating_ledgers(self, db: Session):
        from app.services.tally_sync import sync_tally_ledgers

        company = _make_company(db)

        def fail(r: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=r)

        svc = _make_service(fail)
        log = sync_tally_ledgers(db, company.id, "PAL Test Company", service=svc)

        assert log.error_message is not None
        assert log.ledgers_created == 0
        assert db.query(Ledger).filter(Ledger.company_id == company.id).count() == 0

    def test_manual_ledger_is_not_deleted_on_sync(self, db: Session):
        from app.services.tally_sync import sync_tally_ledgers

        company = _make_company(db)
        manual = Ledger(
            company_id=company.id,
            name="Manual Ledger",
            source=LedgerSource.MANUAL,
        )
        db.add(manual)
        db.commit()

        svc = _make_service(lambda r: httpx.Response(200, text=LEDGER_XML))
        sync_tally_ledgers(db, company.id, "PAL Test Company", service=svc)

        assert db.query(Ledger).filter(Ledger.company_id == company.id).count() == 3
        assert db.query(Ledger).filter(
            Ledger.company_id == company.id, Ledger.name == "Manual Ledger"
        ).one().source == LedgerSource.MANUAL


class TestTallyCompanySyncService:
    def test_first_sync_creates_companies(self, db: Session):
        from app.services.tally_sync import sync_tally_companies

        svc = _make_service(lambda r: httpx.Response(200, text=COMPANY_XML))

        result = sync_tally_companies(db, service=svc)

        assert result.success is True
        assert result.companies_fetched == 2
        assert result.companies_created == 2
        assert {company.name for company in db.query(Company).all()} == {
            "PAL Test Company",
            "Second Co",
        }

    def test_repeated_sync_does_not_duplicate_companies(self, db: Session):
        from app.services.tally_sync import sync_tally_companies

        sync_tally_companies(
            db, service=_make_service(lambda r: httpx.Response(200, text=COMPANY_XML))
        )
        result = sync_tally_companies(
            db, service=_make_service(lambda r: httpx.Response(200, text=COMPANY_XML))
        )

        assert result.companies_created == 0
        assert result.companies_unchanged == 2
        assert db.query(Company).count() == 2

    def test_existing_company_is_matched_case_insensitively_and_reactivated(
        self, db: Session
    ):
        from app.models.company import CompanyStatus
        from app.services.tally_sync import sync_tally_companies

        existing = Company(
            name="pal test company",
            legal_name="",
            status=CompanyStatus.INACTIVE,
        )
        db.add(existing)
        db.commit()

        result = sync_tally_companies(
            db, service=_make_service(lambda r: httpx.Response(200, text=COMPANY_XML))
        )

        db.refresh(existing)
        assert result.companies_created == 1
        assert result.companies_updated == 1
        assert existing.name == "PAL Test Company"
        assert existing.legal_name == "PAL Test Company"
        assert existing.status == CompanyStatus.ACTIVE

    def test_tally_failure_returns_error_without_creating_companies(self, db: Session):
        from app.services.tally_sync import sync_tally_companies

        def fail(r: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=r)

        result = sync_tally_companies(db, service=_make_service(fail))

        assert result.success is False
        assert result.error_message is not None
        assert result.companies_created == 0
        assert db.query(Company).count() == 0

    def test_empty_tally_company_list_is_a_successful_noop(self, db: Session):
        from app.services.tally_sync import sync_tally_companies

        result = sync_tally_companies(
            db, service=_make_service(lambda r: httpx.Response(200, text=EMPTY_COMPANY_XML))
        )

        assert result.success is True
        assert result.companies_fetched == 0
        assert result.companies_created == 0


class TestTallyCompanySyncEndpoint:
    def test_sync_companies_returns_counts(self, client: TestClient, monkeypatch):
        monkeypatch.setattr(
            tally_sync_module,
            "get_tally_service",
            lambda: _make_service(lambda r: httpx.Response(200, text=COMPANY_XML)),
        )

        response = client.post("/api/v1/tally/companies/sync")

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "companies_fetched": 2,
            "companies_created": 2,
            "companies_updated": 0,
            "companies_unchanged": 0,
            "error_message": None,
        }

    def test_sync_companies_failure_returns_200_with_error(
        self, client: TestClient, monkeypatch
    ):
        def fail(r: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=r)

        monkeypatch.setattr(
            tally_sync_module, "get_tally_service", lambda: _make_service(fail)
        )

        response = client.post("/api/v1/tally/companies/sync")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error_message"] is not None
        assert data["companies_created"] == 0


class TestTallySyncEndpoint:
    def test_sync_returns_result(self, client: TestClient, db: Session, monkeypatch):
        company = _make_company(db)
        monkeypatch.setattr(
            tally_sync_module,
            "get_tally_service",
            lambda: _make_service(lambda r: httpx.Response(200, text=LEDGER_XML)),
        )

        response = client.post(
            f"/api/v1/tally/sync/{company.id}",
            json={"tally_company_name": "PAL Test Company"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["ledgers_created"] == 2
        assert data["error_message"] is None

    def test_sync_returns_200_with_error_on_tally_failure(
        self, client: TestClient, db: Session, monkeypatch
    ):
        company = _make_company(db)

        def fail(r: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=r)

        monkeypatch.setattr(
            tally_sync_module, "get_tally_service", lambda: _make_service(fail)
        )

        response = client.post(
            f"/api/v1/tally/sync/{company.id}",
            json={"tally_company_name": "PAL Test Company"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error_message"] is not None

    def test_sync_unknown_company_returns_404(self, client: TestClient):
        response = client.post(
            f"/api/v1/tally/sync/{uuid.uuid4()}",
            json={"tally_company_name": "PAL Test Company"},
        )
        assert response.status_code == 404

    def test_sync_latest_returns_null_before_first_sync(
        self, client: TestClient, db: Session
    ):
        company = _make_company(db)
        response = client.get(f"/api/v1/tally/sync/{company.id}/latest")
        assert response.status_code == 200
        assert response.json() == {"sync": None}

    def test_sync_latest_returns_most_recent(
        self, client: TestClient, db: Session, monkeypatch
    ):
        company = _make_company(db)
        monkeypatch.setattr(
            tally_sync_module,
            "get_tally_service",
            lambda: _make_service(lambda r: httpx.Response(200, text=LEDGER_XML)),
        )

        client.post(
            f"/api/v1/tally/sync/{company.id}",
            json={"tally_company_name": "PAL Test Company"},
        )
        response = client.get(f"/api/v1/tally/sync/{company.id}/latest")

        assert response.status_code == 200
        data = response.json()
        assert data["sync"]["ledgers_created"] == 2
        assert data["sync"]["success"] is True


def test_voucher_import_posts_in_accounting_mode() -> None:
    """A posted Purchase/Sales voucher must force accounting (ledger) mode.

    Without ISINVOICE=No, Tally imports a Purchase/Sales voucher in invoice
    mode and expects inventory entries PAL does not send.
    """
    from decimal import Decimal

    from app.integrations.tally.xml import (
        VoucherImportLine,
        build_voucher_import_request,
    )

    xml = build_voucher_import_request(
        company="Acme",
        voucher_date="20240605",
        voucher_type="Purchase",
        voucher_number="INV-1",
        narration=None,
        lines=[
            VoucherImportLine("Purchases", Decimal("100"), True),
            VoucherImportLine("Creditor", Decimal("100"), False),
        ],
        remote_id="abc",
    )
    assert "<ISINVOICE>No</ISINVOICE>" in xml
    assert 'OBJVIEW="Accounting Voucher View"' in xml
