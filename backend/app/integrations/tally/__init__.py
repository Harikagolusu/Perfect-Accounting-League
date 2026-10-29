"""TallyPrime integration over XML-over-HTTP.

Master reads (companies, ledgers, vouchers) are read-only. The one write path
is `TallyService.post_voucher`, which imports a single PAL-approved voucher and
carries PAL's voucher id as REMOTEID for idempotency. Ledger creation and
ledger mapping remain out of scope: PAL never alters a Tally master.
"""

from app.integrations.tally.client import TallyClient, safe_display_url
from app.integrations.tally.exceptions import (
    TallyConfigurationError,
    TallyConnectionError,
    TallyError,
    TallyHTTPError,
    TallyResponseError,
    TallyTimeoutError,
)
from app.integrations.tally.parser import (
    TallyCompany,
    TallyImportResult,
    TallyLedger,
    TallyLedgerResult,
    TallyVoucher,
    TallyVoucherEntry,
    TallyVoucherResult,
    parse_companies,
    parse_import_result,
    parse_ledgers,
    parse_vouchers,
)
from app.integrations.tally.service import TallyService, get_tally_service
from app.integrations.tally.xml import (
    VoucherImportLine,
    build_company_collection_request,
    build_ledger_collection_request,
    build_voucher_collection_request,
    build_voucher_import_request,
)

__all__ = [
    "TallyClient",
    "TallyCompany",
    "TallyConfigurationError",
    "TallyConnectionError",
    "TallyError",
    "TallyHTTPError",
    "TallyImportResult",
    "TallyLedger",
    "TallyLedgerResult",
    "TallyResponseError",
    "TallyService",
    "TallyTimeoutError",
    "TallyVoucher",
    "TallyVoucherEntry",
    "TallyVoucherResult",
    "VoucherImportLine",
    "build_company_collection_request",
    "build_ledger_collection_request",
    "build_voucher_collection_request",
    "build_voucher_import_request",
    "get_tally_service",
    "parse_companies",
    "parse_import_result",
    "parse_ledgers",
    "parse_vouchers",
    "safe_display_url",
]
