/**
 * Company-scoped Ledger Master API.
 *
 * Every request carries the owning company id, so a ledger id from another
 * company can never be addressed by this client. The backend enforces the same
 * boundary and returns 404 for a foreign id.
 */

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";

export type LedgerSource = "MANUAL" | "IMPORT" | "TALLY" | "ZOHO";

export interface LedgerDto {
  id: string;
  company_id: string;
  name: string;
  code: string | null;
  ledger_type: string | null;
  parent_group: string | null;
  tax_classification: string | null;
  source: LedgerSource;
  external_tally_id: string | null;
  external_zoho_id: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface LedgerWriteDto {
  name?: string;
  code?: string | null;
  ledger_type?: string | null;
  parent_group?: string | null;
  tax_classification?: string | null;
  source?: LedgerSource;
  external_tally_id?: string | null;
  external_zoho_id?: string | null;
  is_active?: boolean;
}

export interface Ledger {
  id: string;
  companyId: string;
  name: string;
  code: string;
  ledgerType: string;
  parentGroup: string;
  taxClassification: string;
  source: LedgerSource;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
}

const blank = (value: string | null | undefined): string => value ?? "";
const nullIfBlank = (value: string): string | null => value.trim() || null;

export const toLedger = (dto: LedgerDto): Ledger => ({
  id: dto.id,
  companyId: dto.company_id,
  name: dto.name,
  code: blank(dto.code),
  ledgerType: blank(dto.ledger_type),
  parentGroup: blank(dto.parent_group),
  taxClassification: blank(dto.tax_classification),
  source: dto.source,
  isActive: dto.is_active,
  createdAt: dto.created_at,
  updatedAt: dto.updated_at,
});

export const toLedgerPayload = (ledger: Partial<Ledger>): LedgerWriteDto => {
  const payload: LedgerWriteDto = {};
  if (ledger.name !== undefined) payload.name = ledger.name.trim();
  if ("code" in ledger) payload.code = nullIfBlank(ledger.code ?? "");
  if ("ledgerType" in ledger) payload.ledger_type = nullIfBlank(ledger.ledgerType ?? "");
  if ("parentGroup" in ledger) payload.parent_group = nullIfBlank(ledger.parentGroup ?? "");
  if ("taxClassification" in ledger)
    payload.tax_classification = nullIfBlank(ledger.taxClassification ?? "");
  if ("source" in ledger && ledger.source) payload.source = ledger.source;
  if ("isActive" in ledger && ledger.isActive !== undefined) payload.is_active = ledger.isActive;
  return payload;
};

const base = (companyId: string) => `${API_V1_PREFIX}/companies/${companyId}/ledgers`;

export const ledgersApi = {
  list: (companyId: string, includeInactive = false) =>
    apiFetch<LedgerDto[]>(base(companyId), {}, { include_inactive: includeInactive }),
  create: (companyId: string, body: LedgerWriteDto) =>
    apiFetch<LedgerDto>(base(companyId), { method: "POST", body: JSON.stringify(body) }),
  update: (companyId: string, ledgerId: string, body: LedgerWriteDto) =>
    apiFetch<LedgerDto>(`${base(companyId)}/${ledgerId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deactivate: (companyId: string, ledgerId: string) =>
    apiFetch<LedgerDto>(`${base(companyId)}/${ledgerId}`, { method: "DELETE" }),
};
