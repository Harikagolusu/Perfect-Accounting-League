/**
 * Document Inbox API contract and the mapper between the backend's
 * snake_case payloads and the camelCase `InboxDocument` the UI already uses.
 *
 * Like vendors, every route is company-scoped: the owning company is part of
 * the address, so the UI cannot fetch documents without saying whose they are.
 *
 * Nothing here interprets the file. The backend only validates and stores
 * bytes, so a document's type stays UNKNOWN until a later processing step
 * classifies it.
 */

import { API_BASE_URL, API_V1_PREFIX, apiFetch } from "@/lib/api/client";
import type { InboxDocument } from "@/lib/types";

/** Mirrors `DocumentStatus` in backend/app/models/document.py. */
export type DocumentStatus = "UPLOADED" | "PROCESSING" | "PROCESSED" | "FAILED";

/** Mirrors `DocumentType` in backend/app/models/document.py. */
export type DocumentType = "UNKNOWN" | "INVOICE" | "BANK_STATEMENT" | "OTHER";

/** A document exactly as the backend returns it. */
export interface DocumentDto {
  id: string;
  company_id: string;
  original_filename: string;
  file_type: string;
  file_size: number;
  document_type: DocumentType;
  status: DocumentStatus;
  created_at: string;
  updated_at: string;
}

export type DocumentUiStatus = InboxDocument["status"];
export type DocumentUiType = NonNullable<InboxDocument["documentType"]>;

/**
 * The inbox shows "Uploaded" for a freshly stored file; nothing is queued for
 * processing yet, so PROCESSING/PROCESSED only appear if a later step moves a
 * document along.
 */
const UI_STATUS: Record<DocumentStatus, DocumentUiStatus> = {
  UPLOADED: "Uploaded",
  PROCESSING: "Processing",
  PROCESSED: "Extracted",
  FAILED: "Failed",
};

const API_STATUS: Record<DocumentUiStatus, DocumentStatus> = {
  Uploaded: "UPLOADED",
  Received: "UPLOADED",
  Queued: "UPLOADED",
  Processing: "PROCESSING",
  Extracted: "PROCESSED",
  "Needs Review": "PROCESSED",
  Approved: "PROCESSED",
  Exported: "PROCESSED",
  Failed: "FAILED",
};

const UI_DOCUMENT_TYPE: Record<DocumentType, DocumentUiType> = {
  UNKNOWN: "Unknown",
  INVOICE: "Invoice",
  BANK_STATEMENT: "Bank Statement",
  OTHER: "Other",
};

/** Short labels the existing table and filters already use. */
const FILE_TYPE_LABELS: Record<string, string> = {
  "application/pdf": "PDF",
  "image/jpeg": "JPEG",
  "image/png": "PNG",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
  "application/vnd.ms-excel": "XLS",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX",
};

export const statusToApi = (status: DocumentUiStatus): DocumentStatus =>
  API_STATUS[status] ?? "UPLOADED";
export const statusToUi = (status: DocumentStatus): DocumentUiStatus =>
  UI_STATUS[status] ?? "Uploaded";
export const documentTypeToUi = (type: DocumentType): DocumentUiType =>
  UI_DOCUMENT_TYPE[type] ?? "Unknown";

/** Display label for a MIME type, falling back to its subtype. */
export const fileTypeLabel = (fileType: string): string => {
  const known = FILE_TYPE_LABELS[fileType];
  if (known) return known;
  const [, subtype] = fileType.split("/");
  return subtype ? subtype.toUpperCase() : fileType.toUpperCase();
};

/** The format labels the inbox filter offers, in display form. */
export const FILE_TYPE_OPTIONS: readonly string[] = ["PDF", "JPEG", "PNG", "XLSX", "XLS", "DOCX"];

/** API payload -> UI model. */
export const toInboxDocument = (dto: DocumentDto): InboxDocument => ({
  id: dto.id,
  companyId: dto.company_id,
  name: dto.original_filename,
  // Email intake is a later step; everything in the inbox arrives by upload.
  source: "Manual Upload",
  receivedAt: dto.created_at,
  fileType: fileTypeLabel(dto.file_type),
  documentType: documentTypeToUi(dto.document_type),
  status: statusToUi(dto.status),
  // Reviewer assignment arrives with user management.
  reviewer: "Unassigned",
  size: dto.file_size,
});

const base = (companyId: string) => `${API_V1_PREFIX}/companies/${companyId}/documents`;

export const documentsApi = {
  list: (
    companyId: string,
    filters: { status?: DocumentStatus; documentType?: DocumentType } = {},
  ) =>
    apiFetch<DocumentDto[]>(
      base(companyId),
      {},
      {
        status: filters.status,
        document_type: filters.documentType,
      },
    ),
  upload: (companyId: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return apiFetch<DocumentDto>(base(companyId), { method: "POST", body });
  },
  /** Inline URL for an iframe/image preview of the stored file. */
  fileUrl: (companyId: string, documentId: string, download = false) =>
    `${API_BASE_URL}${base(companyId)}/${documentId}/file${download ? "?download=true" : ""}`,
  /** Removes both the stored file and the record. */
  remove: (companyId: string, documentId: string) =>
    apiFetch<void>(`${base(companyId)}/${documentId}`, { method: "DELETE" }),
};
