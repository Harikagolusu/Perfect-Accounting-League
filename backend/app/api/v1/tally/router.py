"""TallyPrime connectivity, master-read and sync endpoints.

The Tally URL is read from server-side settings; callers cannot supply one.
Sync writes only to PAL's ledger master for the specified company; it never
modifies invoices, vouchers, or other accounting records.
"""

from __future__ import annotations

import logging
from typing import NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.v1.deps import get_actor
from app.core.config import get_settings
from app.core.database import get_db
from app.integrations.tally import (
    TallyError,
    TallyService,
    get_tally_service,
    safe_display_url,
)
from app.schemas.tally import (
    TallyBankDataSyncRequest,
    TallyBankDataSyncResponse,
    TallyBankSyncResponse,
    TallyCompaniesResponse,
    TallyCompanyResponse,
    TallyCompanySyncResponse,
    TallyLatestSyncResponse,
    TallyLedgerResponse,
    TallyLedgersResponse,
    TallyMappingSyncResponse,
    TallyStatusResponse,
    TallySyncRequest,
    TallySyncResponse,
)
from app.services.company import CompanyNotFoundError
from app.services.tally_mapping_sync import sync_tally_accounting_mappings
from app.services.tally_sync import (
    get_latest_sync,
    sync_tally_bank_accounts,
    sync_tally_bank_data,
    sync_tally_companies,
    sync_tally_ledgers,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_ERROR_STATUS = {
    "configuration_error": status.HTTP_503_SERVICE_UNAVAILABLE,
    "timeout": status.HTTP_504_GATEWAY_TIMEOUT,
    "connection_error": status.HTTP_502_BAD_GATEWAY,
    "http_error": status.HTTP_502_BAD_GATEWAY,
    "invalid_response": status.HTTP_502_BAD_GATEWAY,
}


def _raise_tally_http(exc: TallyError) -> NoReturn:
    """Map a safe Tally error to an HTTP response without leaking details."""
    raise HTTPException(
        status_code=_ERROR_STATUS.get(exc.code, status.HTTP_502_BAD_GATEWAY),
        detail=str(exc),
    ) from None


@router.get(
    "/status",
    response_model=TallyStatusResponse,
    summary="Check TallyPrime connectivity",
)
def tally_status() -> TallyStatusResponse:
    """Actually POST a company-list request to TallyPrime.

    The endpoint always returns HTTP 200: a disconnected Tally is a normal
    operational state for PAL, not a server error. The response contains only
    the configured URL, a boolean and a safe message.
    """
    settings = get_settings()
    service: TallyService | None = None
    connected = False
    try:
        service = get_tally_service()
        connected = service.check_connection()
    except TallyError as exc:
        logger.warning("TallyPrime status check failed: %s", exc)
    finally:
        if service is not None:
            service.close()

    return TallyStatusResponse(
        connected=connected,
        url=safe_display_url(settings.tally_base_url),
        message=(
            "TallyPrime is reachable"
            if connected
            else "Unable to connect to TallyPrime"
        ),
    )


@router.get(
    "/companies",
    response_model=TallyCompaniesResponse,
    summary="Read companies from TallyPrime",
)
def tally_companies() -> TallyCompaniesResponse:
    """Return every company TallyPrime reports for the company collection."""
    service: TallyService | None = None
    try:
        service = get_tally_service()
        companies = service.get_companies()
    except TallyError as exc:
        _raise_tally_http(exc)
    finally:
        if service is not None:
            service.close()

    return TallyCompaniesResponse(
        success=True,
        companies=[TallyCompanyResponse(name=company.name) for company in companies],
        message=None if companies else "No company is loaded in TallyPrime.",
    )


@router.post(
    "/companies/sync",
    response_model=TallyCompanySyncResponse,
    summary="Sync TallyPrime companies into PAL company master",
)
def tally_sync_companies(
    db: Session = Depends(get_db),
) -> TallyCompanySyncResponse:
    """Import TallyPrime companies into PAL's company master.

    Tally is treated as the source of company identity. Companies are matched
    case-insensitively and never deleted. A Tally failure returns HTTP 200 with
    ``success=False`` so the UI can show the problem without a crash page.
    """
    result = sync_tally_companies(db)
    return TallyCompanySyncResponse(
        success=result.success,
        companies_fetched=result.companies_fetched,
        companies_created=result.companies_created,
        companies_updated=result.companies_updated,
        companies_unchanged=result.companies_unchanged,
        error_message=result.error_message,
    )


@router.post(
    "/sync/{company_id}",
    response_model=TallySyncResponse,
    summary="Sync TallyPrime ledgers into PAL ledger master",
)
def tally_sync(
    company_id: UUID,
    body: TallySyncRequest,
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
) -> TallySyncResponse:
    """Fetch ledgers from TallyPrime and upsert into this company's ledger master.

    Existing PAL data is never deleted. Repeated calls are idempotent: a ledger
    already in PAL is updated (if parent changed) or left unchanged, never duplicated.
    On Tally connectivity failure the endpoint returns HTTP 200 with success=False
    and error_message set, so the UI can show the error without treating it as a
    server crash.
    """
    try:
        log = sync_tally_ledgers(db, company_id, body.tally_company_name, actor=actor)
    except CompanyNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return TallySyncResponse(
        success=log.error_message is None,
        company_id=log.company_id,
        tally_company_name=log.tally_company_name,
        synced_at=log.synced_at,
        ledgers_fetched=log.ledgers_fetched,
        ledgers_created=log.ledgers_created,
        ledgers_updated=log.ledgers_updated,
        ledgers_unchanged=log.ledgers_unchanged,
        error_message=log.error_message,
    )


@router.get(
    "/sync/{company_id}/latest",
    response_model=TallyLatestSyncResponse,
    summary="Get the most recent Tally sync result for a company",
)
def tally_sync_latest(
    company_id: UUID,
    db: Session = Depends(get_db),
) -> TallyLatestSyncResponse:
    """Return the most recent sync log row for this company, or null."""
    log = get_latest_sync(db, company_id)
    if log is None:
        return TallyLatestSyncResponse(sync=None)
    return TallyLatestSyncResponse(
        sync=TallySyncResponse(
            success=log.error_message is None,
            company_id=log.company_id,
            tally_company_name=log.tally_company_name,
            synced_at=log.synced_at,
            ledgers_fetched=log.ledgers_fetched,
            ledgers_created=log.ledgers_created,
            ledgers_updated=log.ledgers_updated,
            ledgers_unchanged=log.ledgers_unchanged,
            error_message=log.error_message,
        )
    )


@router.post(
    "/sync/{company_id}/accounting-mappings",
    response_model=TallyMappingSyncResponse,
    summary="Learn ledger mappings from TallyPrime's historical vouchers",
)
def tally_sync_accounting_mappings(
    company_id: UUID,
    body: TallySyncRequest,
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor),
) -> TallyMappingSyncResponse:
    """Record which expense head each party is booked to in Tally's own books.

    Sync the ledger master first: a voucher whose ledgers are not yet in PAL
    cannot contribute. A party whose history is split across heads with no
    clear majority is skipped and stays a reviewer's decision. The read is
    read-only; PAL never writes to TallyPrime.
    """
    try:
        result = sync_tally_accounting_mappings(
            db, company_id, body.tally_company_name, actor=actor
        )
    except CompanyNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return TallyMappingSyncResponse(
        success=result.success,
        company_id=result.company_id,
        tally_company_name=result.tally_company_name,
        vouchers_fetched=result.vouchers_fetched,
        vouchers_used=result.vouchers_used,
        parties_seen=result.parties_seen,
        mappings_created=result.mappings_created,
        mappings_updated=result.mappings_updated,
        mappings_skipped=result.mappings_skipped,
        mappings_preserved=result.mappings_preserved,
        error_message=result.error_message,
    )


@router.post(
    "/sync/{company_id}/bank-accounts",
    response_model=TallyBankSyncResponse,
    summary="Sync Tally bank-account ledgers into PAL bank accounts",
)
def tally_sync_bank_accounts(
    company_id: UUID,
    body: TallySyncRequest,
    db: Session = Depends(get_db),
) -> TallyBankSyncResponse:
    """Import Tally ledgers under 'Bank Accounts' / 'Bank OD Account' into PAL.

    Accounts are matched by ledger_name (case-insensitive) and never deleted.
    Newly created accounts use 'TALLY-IMPORT' as a placeholder account number;
    update the real number through the bank-accounts API after import.
    """
    try:
        result = sync_tally_bank_accounts(db, company_id, body.tally_company_name)
    except CompanyNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return TallyBankSyncResponse(
        success=result.success,
        company_id=company_id,
        tally_company_name=result.tally_company_name,
        accounts_fetched=result.accounts_fetched,
        accounts_created=result.accounts_created,
        accounts_updated=result.accounts_updated,
        accounts_unchanged=result.accounts_unchanged,
        error_message=result.error_message,
    )


@router.post(
    "/sync/{company_id}/bank-data",
    response_model=TallyBankDataSyncResponse,
    summary="Sync Tally bank accounts and bank transactions into PAL",
)
def tally_sync_bank_data(
    company_id: UUID,
    body: TallyBankDataSyncRequest,
    db: Session = Depends(get_db),
) -> TallyBankDataSyncResponse:
    """Import bank-account ledgers and their bank-ledger vouchers from Tally.

    The run creates bank accounts and one Tally-sourced statement per account
    for the requested period, then adds the bank side of every voucher that
    touches one of those ledgers. Repeating the same period is a no-op: each
    voucher line carries a stable external id. PAL never writes to Tally.
    """
    if body.from_date and body.to_date and body.to_date < body.from_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The end date must not be before the start date.",
        )

    try:
        result = sync_tally_bank_data(
            db,
            company_id,
            body.tally_company_name,
            from_date=body.from_date,
            to_date=body.to_date,
        )
    except CompanyNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None

    return TallyBankDataSyncResponse(
        success=result.success,
        company_id=company_id,
        tally_company_name=result.tally_company_name,
        from_date=result.from_date,
        to_date=result.to_date,
        accounts_fetched=result.accounts_fetched,
        accounts_created=result.accounts_created,
        accounts_updated=result.accounts_updated,
        accounts_unchanged=result.accounts_unchanged,
        vouchers_fetched=result.vouchers_fetched,
        vouchers_matched=result.vouchers_matched,
        statements_created=result.statements_created,
        transactions_created=result.transactions_created,
        transactions_skipped=result.transactions_skipped,
        error_message=result.error_message,
    )


@router.get(
    "/ledgers",
    response_model=TallyLedgersResponse,
    summary="Read ledgers from TallyPrime",
)
def tally_ledgers(
    company: str | None = Query(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "Tally company name. When omitted, PAL uses the first company "
            "loaded in TallyPrime."
        ),
    ),
) -> TallyLedgersResponse:
    """Read ledgers for one Tally company.

    PAL does not create ledgers, map them to its own Ledger Master, or post
    anything. The result is a plain read of the Tally master.
    """
    if company is not None:
        company = company.strip()
        if not company:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Company name must not be blank.",
            )

    service: TallyService | None = None
    try:
        service = get_tally_service()
        result = service.get_ledgers(company)
    except TallyError as exc:
        _raise_tally_http(exc)
    finally:
        if service is not None:
            service.close()

    return TallyLedgersResponse(
        success=True,
        company=result.company,
        ledgers=[
            TallyLedgerResponse(name=ledger.name, parent=ledger.parent)
            for ledger in result.ledgers
        ],
        message=result.message,
    )

