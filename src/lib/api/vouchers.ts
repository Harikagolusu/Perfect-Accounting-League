/**
 * Draft voucher, review, approval, export and audit API.
 *
 * The backend owns the lifecycle. The browser only asks for an explicit
 * action; it never sets `POSTED` directly and never calculates a total.
 * Amounts stay decimal strings end to end.
 */

import {
  API_V1_PREFIX,
  apiDownload,
  apiFetch,
  type DownloadedFile,
  type QueryParams,
} from "@/lib/api/client";

export type VoucherStatus =
  | "DRAFT"
  | "PENDING_APPROVAL"
  | "REJECTED"
  | "APPROVED"
  | "POSTING_IN_PROGRESS"
  | "POSTED"
  | "POSTING_FAILED"
  | "AWAITING_IMPORT_CONFIRMATION"
  | "NEEDS_REVIEW";

export type VoucherType = "PURCHASE" | "SALES" | "RECEIPT" | "PAYMENT" | "CONTRA" | "JOURNAL";
export type VoucherSourceType = "INVOICE" | "BANK_TRANSACTION" | "MANUAL";
export type VoucherSide = "Dr" | "Cr";

export interface VoucherIssue {
  code: string;
  message: string;
  severity: "ERROR" | "WARNING";
  field: string | null;
}

export interface VoucherLineDto {
  id: string;
  company_id: string;
  voucher_id: string;
  line_number: number;
  ledger_id: string | null;
  ledger_name: string;
  side: VoucherSide;
  amount: string;
  narration: string | null;
  line_type: string;
  item_name: string | null;
  hsn_sac: string | null;
  billed_quantity: string | null;
  item_rate: string | null;
  item_rate_per: string | null;
  item_amount: string | null;
  tax_type: string | null;
}

export interface VoucherDto {
  id: string;
  company_id: string;
  voucher_date: string;
  voucher_type: VoucherType;
  voucher_number: string;
  status: VoucherStatus;
  narration: string | null;
  source_type: VoucherSourceType;
  source_id: string | null;
  source_document_id: string | null;
  currency: string;
  created_by: string;
  approved_by: string | null;
  approved_at: string | null;
  rejected_by: string | null;
  rejected_at: string | null;
  rejection_reason: string | null;
  posting_reference: string | null;
  posted_at: string | null;
  posting_error: string | null;
  exported_at: string | null;
  exported_by: string | null;
  export_count: number;
  validation_issues: VoucherIssue[];
  total_debit: string;
  total_credit: string;
  is_balanced: boolean;
  lines: VoucherLineDto[];
  created_at: string;
  updated_at: string;
}

export interface VoucherLineWriteDto {
  ledger_id?: string | null;
  ledger_name?: string | null;
  side: VoucherSide;
  amount: string;
  narration?: string | null;
  line_type?: string;
  item_name?: string | null;
  hsn_sac?: string | null;
  billed_quantity?: string | null;
  item_rate?: string | null;
  item_rate_per?: string | null;
  item_amount?: string | null;
  tax_type?: string | null;
}

export interface VoucherWriteDto {
  voucher_date: string;
  voucher_type: VoucherType;
  narration?: string | null;
  currency?: string;
  lines: VoucherLineWriteDto[];
}

export interface VoucherUpdateDto {
  voucher_date?: string;
  voucher_type?: VoucherType;
  narration?: string | null;
  currency?: string;
  lines?: VoucherLineWriteDto[];
}

export interface VoucherActionDto {
  actor?: string;
  comment?: string | null;
  reason?: string | null;
  external_reference?: string | null;
  verified?: boolean | null;
  /** Any day of the posting period; earlier-dated vouchers post on its first. */
  posting_month?: string | null;
}

/** Mirrors `VoucherPostResponse` in backend/app/schemas/voucher.py. */
export interface VoucherPostDto {
  success: boolean;
  voucher_id: string;
  status: VoucherStatus;
  tally_reference: string | null;
  already_posted: boolean;
  message: string;
}

export interface VoucherAuditEventDto {
  id: string;
  company_id: string;
  entity_type: "VOUCHER" | "LEDGER" | "MAPPING";
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

export interface LedgerSuggestionDto {
  purpose: string;
  ledger_id: string | null;
  ledger_name: string | null;
  source: string;
  reason: string;
  code: string | null;
}

export interface LedgerSuggestionResponseDto {
  suggestions: LedgerSuggestionDto[];
  exceptions: VoucherIssue[];
}

export interface VoucherLine {
  id: string;
  companyId: string;
  voucherId: string;
  lineNumber: number;
  ledgerId: string | null;
  ledgerName: string;
  side: VoucherSide;
  amount: string;
  narration: string;
  lineType: string;
  itemName: string;
  hsnSac: string;
  billedQuantity: string;
  itemRate: string;
  itemRatePer: string;
  itemAmount: string;
  taxType: string;
}

export interface Voucher {
  id: string;
  companyId: string;
  voucherDate: string;
  voucherType: VoucherType;
  voucherNumber: string;
  status: VoucherStatus;
  narration: string;
  sourceType: VoucherSourceType;
  sourceId: string | null;
  sourceDocumentId: string | null;
  currency: string;
  createdBy: string;
  approvedBy: string | null;
  approvedAt: string | null;
  rejectedBy: string | null;
  rejectedAt: string | null;
  rejectionReason: string;
  postingReference: string;
  postedAt: string | null;
  postingError: string;
  exportedAt: string | null;
  exportedBy: string | null;
  exportCount: number;
  validationIssues: VoucherIssue[];
  totalDebit: string;
  totalCredit: string;
  isBalanced: boolean;
  lines: VoucherLine[];
  createdAt: string;
  updatedAt: string;
}

export interface VoucherAuditEvent {
  id: string;
  companyId: string;
  entityType: "VOUCHER" | "LEDGER" | "MAPPING";
  entityId: string | null;
  voucherId: string | null;
  eventType: string;
  actor: string;
  previousStatus: string | null;
  newStatus: string | null;
  field: string | null;
  oldValue: string | null;
  newValue: string | null;
  reason: string;
  metadata: Record<string, unknown> | null;
  createdAt: string;
}

export interface LedgerSuggestion {
  purpose: string;
  ledgerId: string | null;
  ledgerName: string;
  source: string;
  reason: string;
  code: string | null;
}

export interface VoucherExportPreview {
  rows: number;
  vouchers: number;
  excluded_records: number;
  exceptions: {
    code: string;
    message: string;
    severity: "ERROR" | "WARNING";
    record_type: string;
    record_id: string | null;
    field: string | null;
  }[];
  warnings: {
    code: string;
    message: string;
    severity: "ERROR" | "WARNING";
    record_type: string;
    record_id: string | null;
    field: string | null;
  }[];
}

export interface VoucherListFilters {
  status?: VoucherStatus;
  voucherType?: VoucherType;
  sourceType?: VoucherSourceType;
  sourceId?: string;
  dateFrom?: string;
  dateTo?: string;
}

const blank = (value: string | null | undefined): string => value ?? "";

const toLine = (dto: VoucherLineDto): VoucherLine => ({
  id: dto.id,
  companyId: dto.company_id,
  voucherId: dto.voucher_id,
  lineNumber: dto.line_number,
  ledgerId: dto.ledger_id,
  ledgerName: dto.ledger_name,
  side: dto.side,
  amount: dto.amount,
  narration: blank(dto.narration),
  lineType: dto.line_type,
  itemName: blank(dto.item_name),
  hsnSac: blank(dto.hsn_sac),
  billedQuantity: blank(dto.billed_quantity),
  itemRate: blank(dto.item_rate),
  itemRatePer: blank(dto.item_rate_per),
  itemAmount: blank(dto.item_amount),
  taxType: blank(dto.tax_type),
});

export const toVoucher = (dto: VoucherDto): Voucher => ({
  id: dto.id,
  companyId: dto.company_id,
  voucherDate: dto.voucher_date,
  voucherType: dto.voucher_type,
  voucherNumber: dto.voucher_number,
  status: dto.status,
  narration: blank(dto.narration),
  sourceType: dto.source_type,
  sourceId: dto.source_id,
  sourceDocumentId: dto.source_document_id,
  currency: dto.currency,
  createdBy: dto.created_by,
  approvedBy: dto.approved_by,
  approvedAt: dto.approved_at,
  rejectedBy: dto.rejected_by,
  rejectedAt: dto.rejected_at,
  rejectionReason: blank(dto.rejection_reason),
  postingReference: blank(dto.posting_reference),
  postedAt: dto.posted_at,
  postingError: blank(dto.posting_error),
  exportedAt: dto.exported_at,
  exportedBy: dto.exported_by,
  exportCount: dto.export_count,
  validationIssues: dto.validation_issues,
  totalDebit: dto.total_debit,
  totalCredit: dto.total_credit,
  isBalanced: dto.is_balanced,
  lines: dto.lines.map(toLine),
  createdAt: dto.created_at,
  updatedAt: dto.updated_at,
});

export const toAuditEvent = (dto: VoucherAuditEventDto): VoucherAuditEvent => ({
  id: dto.id,
  companyId: dto.company_id,
  entityType: dto.entity_type,
  entityId: dto.entity_id,
  voucherId: dto.voucher_id,
  eventType: dto.event_type,
  actor: dto.actor,
  previousStatus: dto.previous_status,
  newStatus: dto.new_status,
  field: dto.field,
  oldValue: dto.old_value,
  newValue: dto.new_value,
  reason: blank(dto.reason),
  metadata: dto.event_metadata,
  createdAt: dto.created_at,
});

export const toLedgerSuggestion = (dto: LedgerSuggestionDto): LedgerSuggestion => ({
  purpose: dto.purpose,
  ledgerId: dto.ledger_id,
  ledgerName: blank(dto.ledger_name),
  source: dto.source,
  reason: dto.reason,
  code: dto.code,
});

export const toLinePayload = (line: VoucherLine): VoucherLineWriteDto => ({
  ledger_id: line.ledgerId,
  ledger_name: line.ledgerId ? null : line.ledgerName || null,
  side: line.side,
  amount: line.amount,
  narration: line.narration || null,
  line_type: line.lineType || "LEDGER",
  item_name: line.itemName || null,
  hsn_sac: line.hsnSac || null,
  billed_quantity: line.billedQuantity || null,
  item_rate: line.itemRate || null,
  item_rate_per: line.itemRatePer || null,
  item_amount: line.itemAmount || null,
  tax_type: line.taxType || null,
});

export const voucherStatusLabel = (status: VoucherStatus): string =>
  ({
    DRAFT: "Draft",
    PENDING_APPROVAL: "Pending approval",
    REJECTED: "Rejected",
    APPROVED: "Approved",
    POSTING_IN_PROGRESS: "Posting in progress",
    POSTED: "Posted",
    POSTING_FAILED: "Posting failed",
    AWAITING_IMPORT_CONFIRMATION: "Awaiting import confirmation",
    NEEDS_REVIEW: "Needs review",
  })[status] ?? status;

export const voucherTypeLabel = (type: VoucherType): string =>
  ({
    PURCHASE: "Purchase",
    SALES: "Sales",
    RECEIPT: "Receipt",
    PAYMENT: "Payment",
    CONTRA: "Contra",
    JOURNAL: "Journal",
  })[type] ?? type;

export const voucherStatusTone = (
  status: VoucherStatus,
): "Approved" | "Pending" | "Needs Review" | "Rejected" | "Exported" | "Draft" => {
  switch (status) {
    case "APPROVED":
      return "Approved";
    case "POSTED":
      return "Approved";
    case "AWAITING_IMPORT_CONFIRMATION":
      return "Exported";
    case "PENDING_APPROVAL":
      return "Pending";
    case "POSTING_IN_PROGRESS":
      return "Pending";
    case "REJECTED":
    case "POSTING_FAILED":
      return "Rejected";
    case "NEEDS_REVIEW":
      return "Needs Review";
    default:
      return "Draft";
  }
};

const base = (companyId: string) => `${API_V1_PREFIX}/companies/${companyId}/vouchers`;

function listParams(filters: VoucherListFilters): QueryParams {
  return {
    status: filters.status,
    voucher_type: filters.voucherType,
    source_type: filters.sourceType,
    source_id: filters.sourceId,
    date_from: filters.dateFrom,
    date_to: filters.dateTo,
  };
}

export const vouchersApi = {
  list: (companyId: string, filters: VoucherListFilters = {}) =>
    apiFetch<VoucherDto[]>(base(companyId), {}, listParams(filters)),
  get: (companyId: string, voucherId: string) =>
    apiFetch<VoucherDto>(`${base(companyId)}/${voucherId}`),
  create: (companyId: string, body: VoucherWriteDto) =>
    apiFetch<VoucherDto>(base(companyId), { method: "POST", body: JSON.stringify(body) }),
  update: (companyId: string, voucherId: string, body: VoucherUpdateDto) =>
    apiFetch<VoucherDto>(`${base(companyId)}/${voucherId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  remove: (companyId: string, voucherId: string) =>
    apiFetch<void>(`${base(companyId)}/${voucherId}`, { method: "DELETE" }),
  fromInvoice: (companyId: string, invoiceId: string, actor = "local-user") =>
    apiFetch<VoucherDto>(`${base(companyId)}/from-invoice/${invoiceId}`, {
      method: "POST",
      body: JSON.stringify({ actor }),
    }),
  fromBank: (
    companyId: string,
    transactionId: string,
    options: { actor?: string; splitLines?: VoucherLineWriteDto[] } = {},
  ) =>
    apiFetch<VoucherDto>(`${base(companyId)}/from-bank/${transactionId}`, {
      method: "POST",
      body: JSON.stringify({
        actor: options.actor ?? "local-user",
        split_lines: options.splitLines ?? null,
      }),
    }),
  submit: (companyId: string, voucherId: string, action: VoucherActionDto = {}) =>
    apiFetch<VoucherDto>(`${base(companyId)}/${voucherId}/submit`, {
      method: "POST",
      body: JSON.stringify(action),
    }),
  approve: (companyId: string, voucherId: string, action: VoucherActionDto = {}) =>
    apiFetch<VoucherDto>(`${base(companyId)}/${voucherId}/approve`, {
      method: "POST",
      body: JSON.stringify(action),
    }),
  reject: (companyId: string, voucherId: string, action: VoucherActionDto = {}) =>
    apiFetch<VoucherDto>(`${base(companyId)}/${voucherId}/reject`, {
      method: "POST",
      body: JSON.stringify(action),
    }),
  reopen: (companyId: string, voucherId: string, action: VoucherActionDto = {}) =>
    apiFetch<VoucherDto>(`${base(companyId)}/${voucherId}/reopen`, {
      method: "POST",
      body: JSON.stringify(action),
    }),
  verifyPosting: (companyId: string, voucherId: string, action: VoucherActionDto) =>
    apiFetch<VoucherDto>(`${base(companyId)}/${voucherId}/verify-posting`, {
      method: "POST",
      body: JSON.stringify(action),
    }),
  audit: (companyId: string, voucherId: string) =>
    apiFetch<VoucherAuditEventDto[]>(`${base(companyId)}/${voucherId}/audit`),
  invoiceSuggestion: (companyId: string, invoiceId: string) =>
    apiFetch<LedgerSuggestionResponseDto>(
      `${API_V1_PREFIX}/companies/${companyId}/invoices/${invoiceId}/ledger-suggestion`,
    ),
  bankSuggestion: (companyId: string, statementId: string, transactionId: string) =>
    apiFetch<LedgerSuggestionResponseDto>(
      `${API_V1_PREFIX}/companies/${companyId}/bank-statements/${statementId}/transactions/${transactionId}/ledger-suggestion`,
    ),
  exportPreview: (
    companyId: string,
    options: { dateFrom?: string; dateTo?: string; postingMonth?: string } = {},
  ) =>
    apiFetch<VoucherExportPreview>(
      `${base(companyId)}/export/preview`,
      {},
      {
        date_from: options.dateFrom || undefined,
        date_to: options.dateTo || undefined,
        posting_month: options.postingMonth || undefined,
      },
    ),
  exportFile: (
    companyId: string,
    body: {
      voucherIds?: string[];
      dateFrom?: string;
      dateTo?: string;
      postingMonth?: string;
      actor?: string;
    } = {},
  ): Promise<DownloadedFile> =>
    apiDownload(`${base(companyId)}/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        voucher_ids: body.voucherIds ?? null,
        date_from: body.dateFrom || null,
        date_to: body.dateTo || null,
        posting_month: body.postingMonth || null,
        actor: body.actor ?? "local-user",
      }),
    }),
  exportOne: (
    companyId: string,
    voucherId: string,
    action: VoucherActionDto = {},
  ): Promise<DownloadedFile> =>
    apiDownload(`${base(companyId)}/${voucherId}/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(action),
    }),
  /**
   * Post one approved voucher into the connected TallyPrime. The XML is built
   * and sent server-side; nothing is downloaded. Safe to call twice: an
   * already-posted voucher answers with its stored reference.
   */
  postToTally: (
    companyId: string,
    voucherId: string,
    action: VoucherActionDto = {},
  ): Promise<VoucherPostDto> =>
    apiFetch<VoucherPostDto>(`${base(companyId)}/${voucherId}/post-to-tally`, {
      method: "POST",
      body: JSON.stringify(action),
    }),
};
