/**
 * Typed client for the three client-format workbook exports.
 *
 * Preview is separate from download on purpose: the UI can show exceptions
 * before the browser receives a file. The backend excludes invalid records, so
 * a download can still succeed with a partial workbook.
 */

import {
  API_V1_PREFIX,
  apiDownload,
  apiFetch,
  type DownloadedFile,
  type QueryParams,
} from "./client";

export type ExportIssue = {
  code: string;
  message: string;
  severity: "ERROR" | "WARNING";
  record_type: string;
  record_id: string | null;
  field: string | null;
};

export type ExportPreview = {
  rows: number;
  vouchers: number;
  excluded_records: number;
  exceptions: ExportIssue[];
  warnings: ExportIssue[];
};

export type DateRange = {
  dateFrom?: string;
  dateTo?: string;
};

function companyBase(companyId: string): string {
  return `${API_V1_PREFIX}/companies/${companyId}`;
}

function rangeParams(range: DateRange = {}): QueryParams {
  return {
    date_from: range.dateFrom || undefined,
    date_to: range.dateTo || undefined,
  };
}

export const exportsApi = {
  invoicePreview(companyId: string, invoiceId?: string): Promise<ExportPreview> {
    return apiFetch<ExportPreview>(
      `${companyBase(companyId)}/invoices/export/preview`,
      {},
      { invoice_id: invoiceId },
    );
  },

  invoiceFile(companyId: string, invoiceId?: string): Promise<DownloadedFile> {
    return apiDownload(
      `${companyBase(companyId)}/invoices/export.xlsx`,
      {},
      { invoice_id: invoiceId },
    );
  },

  bankPreview(
    companyId: string,
    options: DateRange & { statementId?: string } = {},
  ): Promise<ExportPreview> {
    return apiFetch<ExportPreview>(
      `${companyBase(companyId)}/bank-transactions/export/preview`,
      {},
      { statement_id: options.statementId || undefined, ...rangeParams(options) },
    );
  },

  bankFile(
    companyId: string,
    options: DateRange & { statementId?: string } = {},
  ): Promise<DownloadedFile> {
    return apiDownload(
      `${companyBase(companyId)}/bank-transactions/export.xlsx`,
      {},
      { statement_id: options.statementId || undefined, ...rangeParams(options) },
    );
  },

  accountingPreview(
    companyId: string,
    options: DateRange & { includeInvoices?: boolean; includeBank?: boolean } = {},
  ): Promise<ExportPreview> {
    return apiFetch<ExportPreview>(
      `${companyBase(companyId)}/accounting-vouchers/export/preview`,
      {},
      {
        ...rangeParams(options),
        include_invoices: options.includeInvoices ?? true,
        include_bank: options.includeBank ?? true,
      },
    );
  },

  accountingFile(
    companyId: string,
    options: DateRange & { includeInvoices?: boolean; includeBank?: boolean } = {},
  ): Promise<DownloadedFile> {
    return apiDownload(
      `${companyBase(companyId)}/accounting-vouchers/export.xlsx`,
      {},
      {
        ...rangeParams(options),
        include_invoices: options.includeInvoices ?? true,
        include_bank: options.includeBank ?? true,
      },
    );
  },
};

/** First exception message, suitable for a toast when nothing can be exported. */
export function firstExportError(preview: ExportPreview): string | null {
  return preview.exceptions[0]?.message ?? null;
}
