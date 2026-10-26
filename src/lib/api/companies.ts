/**
 * Companies & Clients API contract and the mapper between the backend's
 * snake_case payloads and the camelCase `Company` shape the UI already uses.
 *
 * All conversion lives here so no component ever touches a raw payload field.
 */

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";
import type { Company, EntityStatus } from "@/lib/types";

/** Lifecycle state, mirroring `CompanyStatus` in backend/app/models/company.py. */
export type CompanyStatus = "ACTIVE" | "INACTIVE" | "ONBOARDING";

/** A company exactly as the backend returns it. */
export interface CompanyDto {
  id: string;
  name: string;
  legal_name: string | null;
  gstin: string | null;
  pan: string | null;
  email: string | null;
  phone: string | null;
  address: string | null;
  cgst_ledger: string | null;
  sgst_ledger: string | null;
  igst_ledger: string | null;
  cess_ledger: string | null;
  round_off_ledger: string | null;
  sales_ledger: string | null;
  financial_year: string | null;
  status: CompanyStatus;
  created_at: string;
  updated_at: string;
}

/** Company-level ledger names used by deterministic voucher generation. */
export interface CompanyAccountingWriteDto {
  cgst_ledger?: string | null;
  sgst_ledger?: string | null;
  igst_ledger?: string | null;
  cess_ledger?: string | null;
  round_off_ledger?: string | null;
  sales_ledger?: string | null;
}

/** The only shape the API accepts on write; unknown keys are rejected. */
export interface CompanyWriteDto {
  name: string;
  legal_name: string | null;
  gstin: string | null;
  pan: string | null;
  email: string | null;
  phone: string | null;
  address: string | null;
  financial_year: string | null;
  status: CompanyStatus;
}

/**
 * The first two characters of a GSTIN are the state code, so `state` is
 * derived rather than stored. Reference data, not application state.
 */
const GSTIN_STATE_CODES: Record<string, string> = {
  "01": "Jammu and Kashmir",
  "02": "Himachal Pradesh",
  "03": "Punjab",
  "04": "Chandigarh",
  "05": "Uttarakhand",
  "06": "Haryana",
  "07": "Delhi",
  "08": "Rajasthan",
  "09": "Uttar Pradesh",
  "10": "Bihar",
  "11": "Sikkim",
  "12": "Arunachal Pradesh",
  "13": "Nagaland",
  "14": "Manipur",
  "15": "Mizoram",
  "16": "Tripura",
  "17": "Meghalaya",
  "18": "Assam",
  "19": "West Bengal",
  "20": "Jharkhand",
  "21": "Odisha",
  "22": "Chhattisgarh",
  "23": "Madhya Pradesh",
  "24": "Gujarat",
  "25": "Daman and Diu",
  "26": "Dadra and Nagar Haveli and Daman and Diu",
  "27": "Maharashtra",
  "28": "Andhra Pradesh",
  "29": "Karnataka",
  "30": "Goa",
  "31": "Lakshadweep",
  "32": "Kerala",
  "33": "Tamil Nadu",
  "34": "Puducherry",
  "35": "Andaman and Nicobar Islands",
  "36": "Telangana",
  "37": "Andhra Pradesh",
  "38": "Ladakh",
  "97": "Other Territory",
};

const UI_STATUS: Record<CompanyStatus, EntityStatus> = {
  ACTIVE: "Active",
  INACTIVE: "Inactive",
  ONBOARDING: "Onboarding",
};

const API_STATUS: Record<EntityStatus, CompanyStatus> = {
  Active: "ACTIVE",
  Inactive: "INACTIVE",
  Onboarding: "ONBOARDING",
};

export const statusToApi = (status: EntityStatus): CompanyStatus => API_STATUS[status];
export const statusToUi = (status: CompanyStatus): EntityStatus => UI_STATUS[status] ?? "Active";

/** The state a GSTIN is registered in, or "" when there is no GSTIN yet. */
export const stateFromGstin = (gstin: string | null | undefined): string =>
  (gstin && GSTIN_STATE_CODES[gstin.slice(0, 2)]) || "";

const nullIfBlank = (value: string): string | null => value.trim() || null;

/**
 * API payload -> UI model.
 *
 * Fields the database does not own (`contactPerson`, `software`,
 * `assignedUsers`) get honest defaults: no integration is configured yet and
 * multi-user assignment is out of scope. They are filled in properly when the
 * Tally/Zoho and permissions steps land, rather than being faked into storage
 * now.
 */
export const toCompany = (dto: CompanyDto): Company => ({
  id: dto.id,
  displayName: dto.name,
  legalName: dto.legal_name ?? "",
  gstin: dto.gstin ?? "",
  pan: dto.pan ?? "",
  email: dto.email ?? "",
  phone: dto.phone ?? "",
  address: dto.address ?? "",
  financialYear: dto.financial_year ?? "",
  state: stateFromGstin(dto.gstin),
  contactPerson: "",
  software: "None",
  assignedUsers: [],
  status: statusToUi(dto.status),
  cgstLedger: dto.cgst_ledger ?? "",
  sgstLedger: dto.sgst_ledger ?? "",
  igstLedger: dto.igst_ledger ?? "",
  cessLedger: dto.cess_ledger ?? "",
  roundOffLedger: dto.round_off_ledger ?? "",
  salesLedger: dto.sales_ledger ?? "",
});

/** UI model -> API payload. Sends only fields the API actually stores. */
export const toCompanyPayload = (company: Company): CompanyWriteDto => ({
  name: company.displayName,
  legal_name: nullIfBlank(company.legalName),
  gstin: nullIfBlank(company.gstin),
  pan: nullIfBlank(company.pan),
  email: nullIfBlank(company.email),
  phone: nullIfBlank(company.phone),
  address: nullIfBlank(company.address),
  financial_year: nullIfBlank(company.financialYear),
  status: statusToApi(company.status),
});

export const companiesApi = {
  list: (includeInactive: boolean) =>
    apiFetch<CompanyDto[]>(`${API_V1_PREFIX}/companies`, {}, { include_inactive: includeInactive }),
  get: (id: string) => apiFetch<CompanyDto>(`${API_V1_PREFIX}/companies/${id}`),
  create: (body: CompanyWriteDto) =>
    apiFetch<CompanyDto>(`${API_V1_PREFIX}/companies`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  update: (id: string, body: CompanyWriteDto) =>
    apiFetch<CompanyDto>(`${API_V1_PREFIX}/companies/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  updateAccounting: (id: string, body: CompanyAccountingWriteDto) =>
    apiFetch<CompanyDto>(`${API_V1_PREFIX}/companies/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  /** Soft delete: the backend sets status to INACTIVE and keeps the row. */
  deactivate: (id: string) =>
    apiFetch<CompanyDto>(`${API_V1_PREFIX}/companies/${id}`, { method: "DELETE" }),
};
