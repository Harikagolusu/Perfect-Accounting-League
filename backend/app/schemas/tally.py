"""Response contracts for the TallyPrime integration."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class TallyStatusResponse(BaseModel):
    """Safe connectivity status. Never contains Tally XML or exceptions."""

    connected: bool
    url: str
    message: str


class TallyCompanyResponse(BaseModel):
    """One company loaded in TallyPrime."""

    name: str


class TallyCompaniesResponse(BaseModel):
    """Company master read result."""

    success: bool
    companies: list[TallyCompanyResponse]
    message: str | None = None


class TallyLedgerResponse(BaseModel):
    """One ledger read from TallyPrime."""

    name: str
    parent: str | None = None


class TallyLedgersResponse(BaseModel):
    """Ledger master read result."""

    success: bool
    company: str | None = None
    ledgers: list[TallyLedgerResponse]
    message: str | None = None


class TallyCompanySyncResponse(BaseModel):
    """Result of one Tally -> PAL company-master sync run."""

    success: bool
    companies_fetched: int
    companies_created: int
    companies_updated: int
    companies_unchanged: int
    error_message: str | None = None


class TallySyncRequest(BaseModel):
    """Request body for POST /tally/sync/{company_id}."""

    tally_company_name: str = Field(min_length=1, max_length=200)


class TallySyncResponse(BaseModel):
    """Result of one Tally→PAL ledger sync run."""

    success: bool
    company_id: uuid.UUID
    tally_company_name: str
    synced_at: datetime
    ledgers_fetched: int
    ledgers_created: int
    ledgers_updated: int
    ledgers_unchanged: int
    error_message: str | None = None


class TallyLatestSyncResponse(BaseModel):
    """Most recent sync result, or null when no sync has run."""

    sync: TallySyncResponse | None = None


class TallyBankSyncResponse(BaseModel):
    """Result of one Tally→PAL bank-account sync run."""

    success: bool
    company_id: uuid.UUID
    tally_company_name: str
    accounts_fetched: int
    accounts_created: int
    accounts_updated: int
    accounts_unchanged: int
    error_message: str | None = None


class TallyMappingSyncResponse(BaseModel):
    """Result of one run that learns ledger mappings from Tally vouchers."""

    success: bool
    company_id: uuid.UUID
    tally_company_name: str
    vouchers_fetched: int
    vouchers_used: int
    parties_seen: int
    mappings_created: int
    mappings_updated: int
    mappings_skipped: int
    mappings_preserved: int
    error_message: str | None = None


class TallyBankDataSyncRequest(TallySyncRequest):
    """Request body for POST /tally/sync/{company_id}/bank-data.

    Both dates are optional; when omitted the current financial year is used.
    """

    from_date: date | None = None
    to_date: date | None = None


class TallyBankDataSyncResponse(BaseModel):
    """Result of one Tally→PAL bank-account and bank-transaction sync run."""

    success: bool
    company_id: uuid.UUID
    tally_company_name: str
    from_date: date
    to_date: date
    accounts_fetched: int
    accounts_created: int
    accounts_updated: int
    accounts_unchanged: int
    vouchers_fetched: int
    vouchers_matched: int
    statements_created: int
    transactions_created: int
    transactions_skipped: int
    error_message: str | None = None
