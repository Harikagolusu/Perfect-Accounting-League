/**
 * Invoice Processing API contract and the mapper between the backend's
 * snake_case payloads and the camelCase shape the UI renders.
 *
 * Money arrives as a decimal *string* and stays a string all the way to the
 * input that edits it. Converting to a JavaScript number would reintroduce
 * binary floating point in the browser, which is exactly what the backend's
 * NUMERIC columns exist to avoid.
 */

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";

/** Mirrors `InvoiceStatus` in backend/app/models/invoice.py. */
export type InvoiceStatus = "DRAFT" | "PROCESSING" | "PROCESSED" | "NEEDS_REVIEW" | "FAILED";

/**
 * Mirrors `InvoiceSource`. MANUAL means a person typed the values in; AI means
 * the configured vision provider read them from the source document.
 */
export type InvoiceSource = "MANUAL" | "AI";

/** Mirrors `VendorMappingStatus` in backend/app/models/invoice.py. */
export type VendorMappingStatus =
  "UNMAPPED" | "SUGGESTED" | "MATCHED" | "CONFIRMED" | "REJECTED" | "AMBIGUOUS";

/** Mirrors `VendorMatchMethod` in backend/app/models/invoice.py. */
export type VendorMatchMethod = "EXACT_GSTIN" | "EXACT_PAN" | "NORMALIZED_NAME";

/** Mirrors the read-only match preview in backend/app/schemas/invoice.py. */
export type VendorMatchStatus = "MATCHED" | "NO_MATCH" | "AMBIGUOUS";

export interface VendorMatchCandidateDto {
  id: string;
  name: string;
  gstin: string | null;
  pan: string | null;
  parent_group: string | null;
  default_expense_ledger: string | null;
  creditor_ledger: string | null;
}

export interface VendorMatchDto {
  status: VendorMatchStatus;
  reason: string;
  method: VendorMatchMethod | null;
  vendor_id: string | null;
  requires_confirmation: boolean;
  candidates: VendorMatchCandidateDto[];
}

export interface VendorMatchCandidate {
  id: string;
  name: string;
  gstin: string;
  pan: string;
  parentGroup: string;
  defaultExpenseLedger: string;
  creditorLedger: string;
}

export interface VendorMatch {
  status: VendorMatchStatus;
  reason: string;
  method: VendorMatchMethod | null;
  vendorId: string | null;
  requiresConfirmation: boolean;
  candidates: VendorMatchCandidate[];
}

export interface InvoiceDto {
  id: string;
  company_id: string;
  document_id: string;
  vendor_id: string | null;
  suggested_vendor_id: string | null;
  previous_vendor_id: string | null;
  vendor_mapping_status: VendorMappingStatus;
  vendor_match_method: VendorMatchMethod | null;
  invoice_number: string | null;
  invoice_date: string | null;
  due_date: string | null;
  vendor_name: string | null;
  vendor_gstin: string | null;
  vendor_pan: string | null;
  buyer_gstin: string | null;
  po_number: string | null;
  currency: string;
  taxable_amount: string | null;
  cgst_amount: string | null;
  sgst_amount: string | null;
  igst_amount: string | null;
  cess_amount: string | null;
  round_off: string | null;
  total_amount: string | null;
  status: InvoiceStatus;
  source: InvoiceSource;
  created_at: string;
  updated_at: string;
}

/** The only shape the API accepts on write; unknown keys are rejected. */
export interface InvoiceWriteDto {
  document_id?: string;
  vendor_id?: string | null;
  invoice_number?: string | null;
  invoice_date?: string | null;
  due_date?: string | null;
  vendor_name?: string | null;
  vendor_gstin?: string | null;
  vendor_pan?: string | null;
  buyer_gstin?: string | null;
  po_number?: string | null;
  currency?: string;
  taxable_amount?: string | null;
  cgst_amount?: string | null;
  sgst_amount?: string | null;
  igst_amount?: string | null;
  cess_amount?: string | null;
  round_off?: string | null;
  total_amount?: string | null;
  status?: InvoiceStatus;
}

export interface ProcessResult {
  document_id: string;
  invoice_id: string | null;
  configured: boolean;
  status: "PROCESSED" | "NEEDS_REVIEW" | "FAILED" | "NOT_CONFIGURED";
  reason: string | null;
  message: string;
  issues: string[];
  is_invoice: boolean | null;
  provider: string | null;
  model: string | null;
  usage: Record<string, number> | null;
}

/** The editable string forms the UI binds to. */
export interface InvoiceFormValues {
  invoiceNumber: string;
  invoiceDate: string;
  dueDate: string;
  vendorName: string;
  vendorGstin: string;
  vendorPan: string;
  buyerGstin: string;
  poNumber: string;
  currency: string;
  taxableAmount: string;
  cgstAmount: string;
  sgstAmount: string;
  igstAmount: string;
  cessAmount: string;
  roundOff: string;
  totalAmount: string;
}

const blank = (v: string | null | undefined): string => v ?? "";

/** API payload -> UI model. Field names are converted once, here. */
export const toInvoice = (dto: InvoiceDto) => ({
  id: dto.id,
  companyId: dto.company_id,
  documentId: dto.document_id,
  vendorId: dto.vendor_id,
  suggestedVendorId: dto.suggested_vendor_id,
  previousVendorId: dto.previous_vendor_id,
  vendorMappingStatus: dto.vendor_mapping_status,
  vendorMatchMethod: dto.vendor_match_method,
  invoiceNumber: dto.invoice_number,
  invoiceDate: dto.invoice_date,
  dueDate: dto.due_date,
  vendorName: dto.vendor_name,
  vendorGstin: dto.vendor_gstin,
  vendorPan: dto.vendor_pan,
  buyerGstin: dto.buyer_gstin,
  poNumber: dto.po_number,
  currency: dto.currency,
  taxableAmount: dto.taxable_amount,
  cgstAmount: dto.cgst_amount,
  sgstAmount: dto.sgst_amount,
  igstAmount: dto.igst_amount,
  cessAmount: dto.cess_amount,
  roundOff: dto.round_off,
  totalAmount: dto.total_amount,
  status: dto.status,
  source: dto.source,
  createdAt: dto.created_at,
  updatedAt: dto.updated_at,
});

export type UiInvoice = ReturnType<typeof toInvoice>;

const candidateFromDto = (dto: VendorMatchCandidateDto): VendorMatchCandidate => ({
  id: dto.id,
  name: dto.name,
  gstin: blank(dto.gstin),
  pan: blank(dto.pan),
  parentGroup: blank(dto.parent_group),
  defaultExpenseLedger: blank(dto.default_expense_ledger),
  creditorLedger: blank(dto.creditor_ledger),
});

/** Read-only deterministic match preview -> UI model. */
export const toVendorMatch = (dto: VendorMatchDto): VendorMatch => ({
  status: dto.status,
  reason: dto.reason,
  method: dto.method,
  vendorId: dto.vendor_id,
  requiresConfirmation: dto.requires_confirmation,
  candidates: dto.candidates.map(candidateFromDto),
});

/** UI edit form -> API payload. Empty strings become null. */
export const toInvoicePayload = (form: Partial<InvoiceFormValues>): InvoiceWriteDto => {
  const optional = (v: string | undefined): string | null => {
    const trimmed = (v ?? "").trim();
    return trimmed === "" ? null : trimmed;
  };
  const payload: InvoiceWriteDto = {};
  if ("invoiceNumber" in form) payload.invoice_number = optional(form.invoiceNumber);
  if ("invoiceDate" in form) payload.invoice_date = optional(form.invoiceDate);
  if ("dueDate" in form) payload.due_date = optional(form.dueDate);
  if ("vendorName" in form) payload.vendor_name = optional(form.vendorName);
  if ("vendorGstin" in form) payload.vendor_gstin = optional(form.vendorGstin);
  if ("vendorPan" in form) payload.vendor_pan = optional(form.vendorPan);
  if ("buyerGstin" in form) payload.buyer_gstin = optional(form.buyerGstin);
  if ("poNumber" in form) payload.po_number = optional(form.poNumber);
  if ("currency" in form && form.currency) payload.currency = form.currency.trim().toUpperCase();
  if ("taxableAmount" in form) payload.taxable_amount = optional(form.taxableAmount);
  if ("cgstAmount" in form) payload.cgst_amount = optional(form.cgstAmount);
  if ("sgstAmount" in form) payload.sgst_amount = optional(form.sgstAmount);
  if ("igstAmount" in form) payload.igst_amount = optional(form.igstAmount);
  if ("cessAmount" in form) payload.cess_amount = optional(form.cessAmount);
  if ("roundOff" in form) payload.round_off = optional(form.roundOff);
  if ("totalAmount" in form) payload.total_amount = optional(form.totalAmount);
  return payload;
};

/** Invoice -> the edit form's string values. */
export const invoiceToForm = (invoice: UiInvoice): InvoiceFormValues => ({
  invoiceNumber: blank(invoice.invoiceNumber),
  invoiceDate: blank(invoice.invoiceDate),
  dueDate: blank(invoice.dueDate),
  vendorName: blank(invoice.vendorName),
  vendorGstin: blank(invoice.vendorGstin),
  vendorPan: blank(invoice.vendorPan),
  buyerGstin: blank(invoice.buyerGstin),
  poNumber: blank(invoice.poNumber),
  currency: invoice.currency,
  taxableAmount: blank(invoice.taxableAmount),
  cgstAmount: blank(invoice.cgstAmount),
  sgstAmount: blank(invoice.sgstAmount),
  igstAmount: blank(invoice.igstAmount),
  cessAmount: blank(invoice.cessAmount),
  roundOff: blank(invoice.roundOff),
  totalAmount: blank(invoice.totalAmount),
});

/** Display label for StatusBadge, which already understands these words. */
const UI_STATUS_LABEL: Record<InvoiceStatus, string> = {
  DRAFT: "Draft",
  PROCESSING: "Processing",
  PROCESSED: "Processed",
  NEEDS_REVIEW: "Needs Review",
  FAILED: "Failed",
};

export const statusToUiLabel = (status: InvoiceStatus): string => UI_STATUS_LABEL[status] ?? status;

const MAPPING_STATUS_LABEL: Record<VendorMappingStatus, string> = {
  UNMAPPED: "No match",
  SUGGESTED: "Suggestion",
  MATCHED: "Matched",
  CONFIRMED: "Confirmed",
  REJECTED: "Rejected",
  AMBIGUOUS: "Needs review",
};

export const mappingStatusLabel = (status: VendorMappingStatus): string =>
  MAPPING_STATUS_LABEL[status] ?? status;

export const matchMethodLabel = (method: VendorMatchMethod | null): string => {
  if (method === "EXACT_GSTIN") return "Matched by GSTIN";
  if (method === "EXACT_PAN") return "Matched by PAN";
  if (method === "NORMALIZED_NAME") return "Matched by normalized name";
  return "Confirmed manually";
};

/**
 * Explains how the current mapping came about. No confidence percentage is
 * invented: the backend only records deterministic evidence.
 */
export const mappingSummaryLabel = (
  status: VendorMappingStatus,
  method: VendorMatchMethod | null,
): string => {
  if (status === "SUGGESTED") return "Suggested by normalized name";
  if (status === "MATCHED") {
    if (method === "NORMALIZED_NAME") return "Matched from confirmed history";
    return matchMethodLabel(method);
  }
  if (status === "CONFIRMED") {
    if (method === "NORMALIZED_NAME") return "Confirmed from name suggestion";
    return "Confirmed manually";
  }
  if (status === "REJECTED") return "Suggestion rejected";
  if (status === "AMBIGUOUS") return "Manual review required";
  return "No vendor match";
};

export const STATUS_OPTIONS: readonly InvoiceStatus[] = [
  "DRAFT",
  "PROCESSING",
  "NEEDS_REVIEW",
  "PROCESSED",
  "FAILED",
];

const base = (companyId: string) => `${API_V1_PREFIX}/companies/${companyId}/invoices`;

export const invoicesApi = {
  list: (
    companyId: string,
    filters: { status?: InvoiceStatus; vendorId?: string; documentId?: string } = {},
  ) =>
    apiFetch<InvoiceDto[]>(
      base(companyId),
      {},
      {
        status: filters.status,
        vendor_id: filters.vendorId,
        document_id: filters.documentId,
      },
    ),
  get: (companyId: string, invoiceId: string) =>
    apiFetch<InvoiceDto>(`${base(companyId)}/${invoiceId}`),
  create: (companyId: string, body: InvoiceWriteDto) =>
    apiFetch<InvoiceDto>(base(companyId), { method: "POST", body: JSON.stringify(body) }),
  update: (companyId: string, invoiceId: string, body: InvoiceWriteDto) =>
    apiFetch<InvoiceDto>(`${base(companyId)}/${invoiceId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  remove: (companyId: string, invoiceId: string) =>
    apiFetch<void>(`${base(companyId)}/${invoiceId}`, { method: "DELETE" }),
  /** Asks the backend to extract automatically from the source document. */
  process: (companyId: string, invoiceId: string) =>
    apiFetch<ProcessResult>(`${base(companyId)}/${invoiceId}/process`, { method: "POST" }),
  /** Preview the current deterministic match and its candidates. */
  match: (companyId: string, invoiceId: string) =>
    apiFetch<VendorMatchDto>(`${base(companyId)}/${invoiceId}/vendor-match`),
  /**
   * Confirm a suggested vendor, assign one manually, or reject a suggestion
   * with `vendorId: null`. The backend verifies company ownership.
   */
  confirmVendorMapping: (companyId: string, invoiceId: string, vendorId: string | null) =>
    apiFetch<InvoiceDto>(`${base(companyId)}/${invoiceId}/vendor-mapping`, {
      method: "POST",
      body: JSON.stringify({ vendor_id: vendorId }),
    }),
};
