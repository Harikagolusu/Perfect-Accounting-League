/**
 * GST workings API.
 *
 * The working is derived from the company's processed invoices on the backend,
 * so the same company and period always return the same numbers. Money arrives
 * as decimal strings, exactly like the invoice API.
 */

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";

export interface GstWorkingRowDto {
  category: string;
  taxable: string;
  igst: string;
  cgst: string;
  sgst: string;
}

export interface GstWorkingDto {
  company_id: string;
  from_date: string | null;
  to_date: string | null;
  invoices_used: number;
  gstr1: GstWorkingRowDto[];
  gstr3b: GstWorkingRowDto[];
}

export const gstApi = {
  working: (companyId: string, dateFrom?: string, dateTo?: string) =>
    apiFetch<GstWorkingDto>(
      `${API_V1_PREFIX}/companies/${companyId}/gst/working`,
      {},
      { date_from: dateFrom || undefined, date_to: dateTo || undefined },
    ),
};
