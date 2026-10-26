import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiDownload, parseContentDisposition } from "@/lib/api/client";
import { exportsApi, firstExportError, type ExportPreview } from "@/lib/api/exports";

const COMPANY = "11111111-1111-1111-1111-111111111111";

const PREVIEW: ExportPreview = {
  rows: 2,
  vouchers: 1,
  excluded_records: 0,
  exceptions: [],
  warnings: [],
};

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("parseContentDisposition", () => {
  it("reads the quoted filename the backend emits", () => {
    expect(
      parseContentDisposition('attachment; filename="PAL_Asterleaf_Invoices_20261024.xlsx"'),
    ).toBe("PAL_Asterleaf_Invoices_20261024.xlsx");
  });

  it("reads an RFC 5987 encoded filename", () => {
    expect(parseContentDisposition("attachment; filename*=UTF-8''PAL%20Asterleaf.xlsx")).toBe(
      "PAL Asterleaf.xlsx",
    );
  });

  it("returns null when no filename is present", () => {
    expect(parseContentDisposition(null)).toBeNull();
    expect(parseContentDisposition("attachment")).toBeNull();
  });
});

describe("apiDownload", () => {
  it("returns the blob and filename without parsing the body as JSON", async () => {
    const blob = new Blob(["xlsx"], {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({
        "content-disposition": 'attachment; filename="PAL_Invoices.xlsx"',
      }),
      blob: async () => blob,
    } as Response);

    const result = await apiDownload("/api/v1/companies/1/invoices/export.xlsx");

    expect(result.blob).toBe(blob);
    expect(result.filename).toBe("PAL_Invoices.xlsx");
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("throws ApiError with the backend detail", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "Company not found" }, 404));

    await expect(apiDownload("/api/v1/companies/missing/invoices/export.xlsx")).rejects.toThrow(
      new ApiError(404, "Company not found"),
    );
  });
});

describe("exportsApi", () => {
  it("scopes the invoice preview and download to one invoice", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(PREVIEW))
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        headers: new Headers({
          "content-disposition": 'attachment; filename="PAL_Invoices.xlsx"',
        }),
        blob: async () => new Blob(["xlsx"]),
      } as Response);

    await exportsApi.invoicePreview(COMPANY, "invoice-1");
    await exportsApi.invoiceFile(COMPANY, "invoice-1");

    const urls = vi.mocked(fetch).mock.calls.map(([url]) => String(url));
    expect(urls[0]).toContain(`/api/v1/companies/${COMPANY}/invoices/export/preview`);
    expect(urls[0]).toContain("invoice_id=invoice-1");
    expect(urls[1]).toContain(`/api/v1/companies/${COMPANY}/invoices/export.xlsx`);
    expect(urls[1]).toContain("invoice_id=invoice-1");
  });

  it("passes the bank statement and date range as query parameters", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(PREVIEW));

    await exportsApi.bankPreview(COMPANY, {
      statementId: "statement-1",
      dateFrom: "2026-03-01",
      dateTo: "2026-03-31",
    });

    const [url] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain("statement_id=statement-1");
    expect(String(url)).toContain("date_from=2026-03-01");
    expect(String(url)).toContain("date_to=2026-03-31");
  });

  it("defaults accounting exports to invoices and bank transactions", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(PREVIEW));

    await exportsApi.accountingPreview(COMPANY);

    const [url] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain("include_invoices=true");
    expect(String(url)).toContain("include_bank=true");
  });

  it("can exclude bank transactions from an accounting export", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(PREVIEW));

    await exportsApi.accountingPreview(COMPANY, { includeBank: false });

    const [url] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain("include_invoices=true");
    expect(String(url)).toContain("include_bank=false");
  });
});

describe("firstExportError", () => {
  it("returns the first blocking exception", () => {
    const preview: ExportPreview = {
      ...PREVIEW,
      exceptions: [
        {
          code: "INVOICE_FIELD_REQUIRED",
          message: "Place of supply is required.",
          severity: "ERROR",
          record_type: "INVOICE",
          record_id: "invoice-1",
          field: "Place of Supply",
        },
      ],
    };

    expect(firstExportError(preview)).toBe("Place of supply is required.");
    expect(firstExportError(PREVIEW)).toBeNull();
  });
});
