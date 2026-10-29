/**
 * TallyPrime connectivity and sync API.
 *
 * The backend owns the Tally URL; the browser only asks for a safe status
 * payload. No Tally XML is ever exposed to this client.
 */

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";

export interface TallyStatusDto {
  connected: boolean;
  url: string;
  message: string;
}

export interface TallySyncDto {
  success: boolean;
  company_id: string;
  tally_company_name: string;
  synced_at: string;
  ledgers_fetched: number;
  ledgers_created: number;
  ledgers_updated: number;
  ledgers_unchanged: number;
  error_message: string | null;
}

export interface TallyCompanySyncDto {
  success: boolean;
  companies_fetched: number;
  companies_created: number;
  companies_updated: number;
  companies_unchanged: number;
  error_message: string | null;
}

export interface TallyBankSyncDto {
  success: boolean;
  company_id: string;
  tally_company_name: string;
  accounts_fetched: number;
  accounts_created: number;
  accounts_updated: number;
  accounts_unchanged: number;
  error_message: string | null;
}

export interface TallyMappingSyncDto {
  success: boolean;
  company_id: string;
  tally_company_name: string;
  vouchers_fetched: number;
  vouchers_used: number;
  parties_seen: number;
  mappings_created: number;
  mappings_updated: number;
  mappings_skipped: number;
  mappings_preserved: number;
  error_message: string | null;
}

export interface TallyBankDataSyncDto {
  success: boolean;
  company_id: string;
  tally_company_name: string;
  from_date: string;
  to_date: string;
  accounts_fetched: number;
  accounts_created: number;
  accounts_updated: number;
  accounts_unchanged: number;
  vouchers_fetched: number;
  vouchers_matched: number;
  statements_created: number;
  transactions_created: number;
  transactions_skipped: number;
  error_message: string | null;
}

export const tallyApi = {
  status: () => apiFetch<TallyStatusDto>(`${API_V1_PREFIX}/tally/status`),
  syncCompanies: () =>
    apiFetch<TallyCompanySyncDto>(`${API_V1_PREFIX}/tally/companies/sync`, {
      method: "POST",
    }),
  sync: (companyId: string, tallyCompanyName: string) =>
    apiFetch<TallySyncDto>(`${API_V1_PREFIX}/tally/sync/${companyId}`, {
      method: "POST",
      body: JSON.stringify({ tally_company_name: tallyCompanyName }),
    }),
  latestSync: (companyId: string) =>
    apiFetch<{ sync: TallySyncDto | null }>(`${API_V1_PREFIX}/tally/sync/${companyId}/latest`),
  syncAccountingMappings: (companyId: string, tallyCompanyName: string) =>
    apiFetch<TallyMappingSyncDto>(
      `${API_V1_PREFIX}/tally/sync/${companyId}/accounting-mappings`,
      {
        method: "POST",
        body: JSON.stringify({ tally_company_name: tallyCompanyName }),
      },
    ),
  syncBankAccounts: (companyId: string, tallyCompanyName: string) =>
    apiFetch<TallyBankSyncDto>(
      `${API_V1_PREFIX}/tally/sync/${companyId}/bank-accounts`,
      {
        method: "POST",
        body: JSON.stringify({ tally_company_name: tallyCompanyName }),
      },
    ),
  syncBankData: (
    companyId: string,
    tallyCompanyName: string,
    fromDate?: string,
    toDate?: string,
  ) =>
    apiFetch<TallyBankDataSyncDto>(
      `${API_V1_PREFIX}/tally/sync/${companyId}/bank-data`,
      {
        method: "POST",
        body: JSON.stringify({
          tally_company_name: tallyCompanyName,
          from_date: fromDate || undefined,
          to_date: toDate || undefined,
        }),
      },
    ),
};
