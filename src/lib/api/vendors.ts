/**
 * Vendor Master API contract and the mapper between the backend's snake_case
 * payloads and the camelCase `Vendor` shape the UI already uses.
 *
 * Every route is company-scoped: the owning company is part of the address, so
 * the UI cannot ask for a vendor without saying which company it belongs to.
 */

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";
import type { Vendor } from "@/lib/types";

/** Mirrors `VendorStatus` in backend/app/models/vendor.py. */
export type VendorStatus = "ACTIVE" | "INACTIVE" | "NEEDS_MAPPING";

/** A vendor exactly as the backend returns it. */
export interface VendorDto {
  id: string;
  company_id: string;
  name: string;
  gstin: string | null;
  pan: string | null;
  default_expense_ledger: string | null;
  creditor_ledger: string | null;
  parent_group: string | null;
  status: VendorStatus;
  created_at: string;
  updated_at: string;
}

/** The only shape the API accepts on write; unknown keys are rejected. */
export interface VendorWriteDto {
  name: string;
  gstin: string | null;
  pan: string | null;
  default_expense_ledger: string | null;
  creditor_ledger: string | null;
  parent_group: string | null;
  status: VendorStatus;
}

/** UI-facing status labels, matching what StatusBadge already renders. */
export type VendorUiStatus = Vendor["status"];

const UI_STATUS: Record<VendorStatus, VendorUiStatus> = {
  ACTIVE: "Active",
  INACTIVE: "Inactive",
  NEEDS_MAPPING: "Needs Mapping",
};

const API_STATUS: Record<VendorUiStatus, VendorStatus> = {
  Active: "ACTIVE",
  Inactive: "INACTIVE",
  "Needs Mapping": "NEEDS_MAPPING",
};

export const statusToApi = (status: VendorUiStatus): VendorStatus => API_STATUS[status];
export const statusToUi = (status: VendorStatus): VendorUiStatus => UI_STATUS[status] ?? "Active";

const nullIfBlank = (value: string): string | null => value.trim() || null;

/**
 * API payload -> UI model.
 *
 * `transactions` and `lastUsed` are derived from invoices, which are not built
 * yet, so the mapper reports honest zeroes rather than inventing numbers. They
 * become real aggregates when the invoice module lands.
 */
export const toVendor = (dto: VendorDto): Vendor => ({
  id: dto.id,
  companyId: dto.company_id,
  name: dto.name,
  gstin: dto.gstin ?? "",
  pan: dto.pan ?? "",
  defaultExpenseLedger: dto.default_expense_ledger ?? "",
  creditorLedger: dto.creditor_ledger ?? "",
  parentGroup: dto.parent_group ?? "",
  transactions: 0,
  lastUsed: "",
  status: statusToUi(dto.status),
});

/** UI model -> API payload. Sends only fields the API actually stores. */
export const toVendorPayload = (vendor: Vendor): VendorWriteDto => ({
  name: vendor.name,
  gstin: nullIfBlank(vendor.gstin),
  pan: nullIfBlank(vendor.pan),
  default_expense_ledger: nullIfBlank(vendor.defaultExpenseLedger),
  creditor_ledger: nullIfBlank(vendor.creditorLedger),
  parent_group: nullIfBlank(vendor.parentGroup),
  status: statusToApi(vendor.status),
});

const base = (companyId: string) => `${API_V1_PREFIX}/companies/${companyId}/vendors`;

export const vendorsApi = {
  list: (companyId: string, includeInactive: boolean) =>
    apiFetch<VendorDto[]>(base(companyId), {}, { include_inactive: includeInactive }),
  get: (companyId: string, vendorId: string) =>
    apiFetch<VendorDto>(`${base(companyId)}/${vendorId}`),
  create: (companyId: string, body: VendorWriteDto) =>
    apiFetch<VendorDto>(base(companyId), { method: "POST", body: JSON.stringify(body) }),
  update: (companyId: string, vendorId: string, body: VendorWriteDto) =>
    apiFetch<VendorDto>(`${base(companyId)}/${vendorId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  /** Soft delete: the backend sets status to INACTIVE and keeps the row. */
  deactivate: (companyId: string, vendorId: string) =>
    apiFetch<VendorDto>(`${base(companyId)}/${vendorId}`, { method: "DELETE" }),
};
