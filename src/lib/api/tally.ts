/**
 * TallyPrime connectivity and sync API.
 *
 * The backend owns the Tally URL; the browser only asks for a safe status
 * payload. No Tally XML is ever exposed to this client.
 */

import { API_V1_PREFIX, apiDownload, apiFetch, type DownloadedFile } from "@/lib/api/client";

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

export interface TallyVendorSyncDto {
  success: boolean;
  company_id: string;
  tally_company_name: string;
  vendors_fetched: number;
  vendors_created: number;
  vendors_updated: number;
  vendors_unchanged: number;
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

export interface GstComponentTotalsDto {
  taxable: string;
  igst: string;
  cgst: string;
  sgst: string;
}

export interface TallyGstWorkingDto {
  success: boolean;
  company_id: string;
  tally_company_name: string;
  from_date: string | null;
  to_date: string | null;
  outward: GstComponentTotalsDto;
  inward_itc: GstComponentTotalsDto;
  vouchers_fetched: number;
  vouchers_used: number;
  error_message: string | null;
}

export interface TallyTdsWorkingRowDto {
  voucher_date: string | null;
  voucher_number: string;
  party_name: string;
  pan: string;
  section: string;
  base: string;
  tds: string;
  rate: string;
  status: string;
}

export interface TallyTdsWorkingDto {
  success: boolean;
  company_id: string;
  tally_company_name: string;
  from_date: string | null;
  to_date: string | null;
  rows: TallyTdsWorkingRowDto[];
  total_tds: string;
  vouchers_fetched: number;
  vouchers_used: number;
  error_message: string | null;
}

export const tallyApi = {
  status: () => apiFetch<TallyStatusDto>(`${API_V1_PREFIX}/tally/status`),
  companies: () =>
    apiFetch<{ success: boolean; companies: { name: string }[] }>(
      `${API_V1_PREFIX}/tally/companies`,
    ),
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
  syncVendors: (companyId: string, tallyCompanyName: string) =>
    apiFetch<TallyVendorSyncDto>(`${API_V1_PREFIX}/tally/sync/${companyId}/vendors`, {
      method: "POST",
      body: JSON.stringify({ tally_company_name: tallyCompanyName }),
    }),
  gstWorking: (companyId: string, tallyCompanyName: string, fromDate?: string, toDate?: string) =>
    apiFetch<TallyGstWorkingDto>(`${API_V1_PREFIX}/tally/sync/${companyId}/gst-working`, {
      method: "POST",
      body: JSON.stringify({
        tally_company_name: tallyCompanyName,
        from_date: fromDate || undefined,
        to_date: toDate || undefined,
      }),
    }),
  // Download the full GST working as an .xlsx in the client's eleven-sheet layout.
  gstWorkbook: (
    companyId: string,
    tallyCompanyName: string,
    fromDate?: string,
    toDate?: string,
  ): Promise<DownloadedFile> =>
    apiDownload(`${API_V1_PREFIX}/tally/sync/${companyId}/gst-working/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tally_company_name: tallyCompanyName,
        from_date: fromDate || undefined,
        to_date: toDate || undefined,
      }),
    }),
  tdsWorking: (companyId: string, tallyCompanyName: string, fromDate?: string, toDate?: string) =>
    apiFetch<TallyTdsWorkingDto>(`${API_V1_PREFIX}/tally/sync/${companyId}/tds-working`, {
      method: "POST",
      body: JSON.stringify({
        tally_company_name: tallyCompanyName,
        from_date: fromDate || undefined,
        to_date: toDate || undefined,
      }),
    }),
  // Download the full TDS working as an .xlsx in the client's three-sheet layout.
  tdsWorkbook: (
    companyId: string,
    tallyCompanyName: string,
    fromDate?: string,
    toDate?: string,
  ): Promise<DownloadedFile> =>
    apiDownload(`${API_V1_PREFIX}/tally/sync/${companyId}/tds-working/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tally_company_name: tallyCompanyName,
        from_date: fromDate || undefined,
        to_date: toDate || undefined,
      }),
    }),
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
