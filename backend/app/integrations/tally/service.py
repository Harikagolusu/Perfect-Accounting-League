"""TallyPrime business operations.

Reads are the bulk of this service: connectivity, companies, ledgers and
vouchers. `post_voucher` is the single write operation, used only for a voucher
PAL has already approved. It creates nothing else in Tally and never alters a
ledger master.
"""

from __future__ import annotations

import logging

from app.integrations.tally.client import TallyClient
from app.integrations.tally.exceptions import TallyConfigurationError, TallyError
from app.integrations.tally.parser import (
    TallyCompany,
    TallyImportResult,
    TallyLedgerResult,
    TallyVoucherResult,
    parse_companies,
    parse_import_result,
    parse_ledgers,
    parse_stock_items,
    parse_voucher_references,
    parse_vouchers,
)
from app.integrations.tally.xml import (
    VoucherImportLine,
    build_company_collection_request,
    build_ledger_collection_request,
    build_ledger_import_request,
    build_stock_item_collection_request,
    build_voucher_collection_request,
    build_voucher_import_request,
    build_voucher_register_request,
)

logger = logging.getLogger(__name__)


class TallyService:
    """Thin, stateless facade over the TallyPrime XML client."""

    def __init__(self, client: TallyClient | None = None) -> None:
        self._client = client or TallyClient()

    def check_connection(self) -> bool:
        """Return True only when Tally answers a real company-list request."""
        try:
            self.get_companies()
        except TallyError as exc:
            logger.warning("TallyPrime connectivity check failed: %s", exc)
            return False
        return True

    def get_companies(self) -> list[TallyCompany]:
        """Read the companies loaded in TallyPrime."""
        raw = self._client.post_xml(build_company_collection_request())
        return parse_companies(raw)

    def get_ledgers(self, company: str | None = None) -> TallyLedgerResult:
        """Read ledgers for ``company`` or the first loaded company.

        Tally's ledger response does not always echo the active company, so
        when no company is supplied PAL resolves it from the company list
        first. This is a POC limitation: with several companies loaded, pass
        ``company`` explicitly to select one.
        """
        if company is not None:
            company = company.strip()
            if not company:
                raise TallyConfigurationError("Company name must not be blank.")
        else:
            companies = self.get_companies()
            if not companies:
                return TallyLedgerResult(
                    company=None,
                    ledgers=[],
                    message="No company is loaded in TallyPrime.",
                )
            company = companies[0].name

        raw = self._client.post_xml(build_ledger_collection_request(company))
        result = parse_ledgers(raw, company=company)
        if not result.ledgers:
            result = TallyLedgerResult(
                company=result.company,
                ledgers=[],
                message="No ledgers were returned by TallyPrime.",
            )
        return result

    def post_voucher(
        self,
        *,
        company: str | None,
        voucher_date: str,
        voucher_type: str,
        voucher_number: str,
        narration: str | None,
        lines: list[VoucherImportLine],
        remote_id: str,
        reference: str | None = None,
        reference_date: str | None = None,
    ) -> TallyImportResult:
        """Import one voucher into TallyPrime and report what Tally said.

        `remote_id` is PAL's voucher id, sent as Tally's REMOTEID so a retry
        matches the voucher already stored instead of duplicating it. Amounts
        and ledger names are passed through untouched.

        Raises TallyError on a transport, HTTP or XML failure. A 200 response
        that Tally itself rejected comes back as a result with errors set, not
        an exception.
        """
        if not voucher_number or not voucher_number.strip():
            raise TallyConfigurationError("Voucher number must not be blank.")
        if not remote_id or not remote_id.strip():
            raise TallyConfigurationError("Remote id must not be blank.")

        payload = build_voucher_import_request(
            company=company.strip() if company else None,
            voucher_date=voucher_date,
            voucher_type=voucher_type,
            voucher_number=voucher_number,
            narration=narration,
            lines=lines,
            remote_id=remote_id,
            reference=reference,
            reference_date=reference_date,
        )
        logger.info("Posting voucher %s to TallyPrime", voucher_number)
        # Request and response carry the company's ledger names and amounts, so
        # they are logged only at DEBUG, which is off in normal operation, and
        # are never returned to the client.
        logger.debug("Tally import request for %s:\n%s", voucher_number, payload)
        raw = self._client.post_xml(payload)
        logger.debug("Tally import response for %s:\n%s", voucher_number, raw)
        return parse_import_result(raw)

    def create_ledger(
        self,
        *,
        name: str,
        parent: str,
        company: str | None = None,
        gstin: str | None = None,
    ) -> TallyImportResult:
        """Create one ledger master in TallyPrime and report what Tally said.

        Raises TallyError on a transport/HTTP/XML failure; a 200 that Tally
        rejected comes back as a result with errors/exceptions set (see
        ``TallyImportResult.accepted``), not an exception.
        """
        payload = build_ledger_import_request(
            company=company.strip() if company else None,
            name=name,
            parent=parent,
            gstin=gstin,
        )
        logger.info("Creating ledger %r in TallyPrime", name)
        logger.debug("Tally ledger import request for %r:\n%s", name, payload)
        raw = self._client.post_xml(payload)
        logger.debug("Tally ledger import response for %r:\n%s", name, raw)
        return parse_import_result(raw)

    def get_vouchers(self, company: str | None = None) -> TallyVoucherResult:
        """Read accounting vouchers for ``company`` or the first loaded company.

        This is a read-only export. TallyPrime returns every voucher in the
        company's books; the caller filters by date and by bank ledger.
        """
        if company is not None:
            company = company.strip()
            if not company:
                raise TallyConfigurationError("Company name must not be blank.")
        else:
            companies = self.get_companies()
            if not companies:
                return TallyVoucherResult(
                    company=None,
                    vouchers=[],
                    message="No company is loaded in TallyPrime.",
                )
            company = companies[0].name

        raw = self._client.post_xml(build_voucher_collection_request(company))
        result = parse_vouchers(raw, company=company)
        if not result.vouchers:
            result = TallyVoucherResult(
                company=result.company,
                vouchers=[],
                message="No vouchers were returned by TallyPrime.",
            )
        return result

    def get_gst_vouchers(
        self,
        company: str | None = None,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
        with_inventory: bool = False,
    ) -> TallyVoucherResult:
        """Read every voucher for the GST working; the caller filters by date.

        ``from_date``/``to_date`` are accepted for the caller's convenience but
        not sent to Tally: the Day Book export honoured them on paper yet, on
        real builds, returned only the current date's single voucher. The full
        voucher collection returns the whole period, and
        ``compute_tally_gst_working`` filters it by ``voucher.date``. Read-only.
        """
        if company is not None:
            company = company.strip()
            if not company:
                raise TallyConfigurationError("Company name must not be blank.")
        else:
            companies = self.get_companies()
            if not companies:
                return TallyVoucherResult(
                    company=None,
                    vouchers=[],
                    message="No company is loaded in TallyPrime.",
                )
            company = companies[0].name

        raw = self._client.post_xml(
            build_voucher_collection_request(company, with_inventory=with_inventory)
        )
        return parse_vouchers(raw, company=company)

    def get_stock_items(self, company: str) -> dict[str, str]:
        """Read stock item name -> HSN/SAC code for a company. Read-only."""
        raw = self._client.post_xml(build_stock_item_collection_request(company))
        return parse_stock_items(raw)

    def get_voucher_references(
        self,
        company: str,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, str]:
        """Read voucher GUID -> Supplier Invoice No. (REFERENCE) for a period.

        Uses the Voucher Register report, the only export that exposes REFERENCE
        for every voucher and honours the date range. Read-only.
        """
        raw = self._client.post_xml(
            build_voucher_register_request(
                company, from_date=from_date, to_date=to_date
            )
        )
        return parse_voucher_references(raw)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TallyService:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def get_tally_service() -> TallyService:
    """Build a Tally service for one request.

    Kept as a factory so tests can monkeypatch it without opening a socket.
    """
    return TallyService()
