/**
 * Company-wide accounting audit trail.
 *
 * The backend records every accounting decision (invoice, vendor, document,
 * ledger, mapping and voucher events) in an append-only table. This reads that
 * persisted trail so the Activity / Audit views survive a restart instead of
 * living in browser memory.
 */

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";
import type { AuditEvent } from "@/lib/types";

export type AccountingEntityType =
  "VOUCHER" | "LEDGER" | "MAPPING" | "INVOICE" | "VENDOR" | "DOCUMENT";

export interface AccountingAuditEventDto {
  id: string;
  company_id: string;
  entity_type: AccountingEntityType;
  entity_id: string | null;
  voucher_id: string | null;
  event_type: string;
  actor: string;
  previous_status: string | null;
  new_status: string | null;
  field: string | null;
  old_value: string | null;
  new_value: string | null;
  reason: string | null;
  event_metadata: Record<string, unknown> | null;
  created_at: string;
}

/** The module label each entity type shows under in the Activity views. */
const MODULE_BY_ENTITY: Record<AccountingEntityType, string> = {
  VOUCHER: "Vouchers",
  LEDGER: "Ledger Master",
  MAPPING: "Vendor Mapping",
  INVOICE: "Invoice Processing",
  VENDOR: "Vendor Master",
  DOCUMENT: "Document Inbox",
};

/** `INVOICE_VENDOR_MAPPING_CONFIRMED` -> `Invoice vendor mapping confirmed`. */
function humanizeAction(eventType: string): string {
  const words = eventType.toLowerCase().replace(/_/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : eventType;
}

export const toAuditEvent = (dto: AccountingAuditEventDto): AuditEvent => ({
  id: dto.id,
  timestamp: dto.created_at,
  user: dto.actor,
  companyId: dto.company_id,
  module: MODULE_BY_ENTITY[dto.entity_type] ?? dto.entity_type,
  action: humanizeAction(dto.event_type),
  recordId: dto.new_value ?? dto.entity_id ?? "—",
  previous: dto.previous_status ?? "—",
  next: dto.new_status ?? "—",
  status: "Success",
});

export const accountingAuditApi = {
  list: (companyId: string, limit = 500) =>
    apiFetch<AccountingAuditEventDto[]>(
      `${API_V1_PREFIX}/companies/${companyId}/accounting-audit/`,
      {},
      { limit },
    ),
};
