import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  canApproveData,
  invoiceToForm,
  mappingNeedsReview,
  mappingStatusLabel,
  mappingSummaryLabel,
  matchMethodLabel,
  statusToUiLabel,
  toInvoice,
  toInvoicePayload,
  toVendorMatch,
  type InvoiceDto,
} from "@/lib/api/invoices";
import {
  useConfirmVendorMapping,
  useCreateInvoice,
  useDeleteInvoice,
  useInvoices,
  useUpdateInvoice,
  useVendorMatch,
} from "@/lib/queries/invoices";

const COMPANY_A = "11111111-1111-1111-1111-111111111111";
const COMPANY_B = "22222222-2222-2222-2222-222222222222";
const DOC_A = "33333333-3333-3333-3333-333333333333";

const INVOICE: InvoiceDto = {
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  company_id: COMPANY_A,
  document_id: DOC_A,
  vendor_id: null,
  suggested_vendor_id: null,
  previous_vendor_id: null,
  vendor_mapping_status: "UNMAPPED",
  vendor_match_method: null,
  invoice_number: "INV-2026-001",
  invoice_date: "2026-03-15",
  due_date: "2026-04-14",
  vendor_name: "Deccan Freight Carriers",
  vendor_gstin: "27AACCD1234F1Z2",
  vendor_pan: "AACCD1234F",
  buyer_gstin: "27AAJCS4821K1Z5",
  po_number: "PO-8891",
  currency: "INR",
  direction: "PURCHASE",
  taxable_amount: "1000.00",
  cgst_amount: "90.00",
  sgst_amount: "90.00",
  igst_amount: null,
  cess_amount: "0.00",
  round_off: "0.00",
  total_amount: "1180.00",
  status: "DRAFT",
  source: "MANUAL",
  created_at: "2026-10-19T06:00:00Z",
  updated_at: "2026-10-19T06:00:00Z",
};

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

const calls = () => vi.mocked(fetch).mock.calls as unknown as [string, RequestInit][];

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function ListHarness({ companyId }: { companyId: string | undefined }) {
  const { data, isPending, isError } = useInvoices(companyId);
  if (isPending) return <p>Loading invoices</p>;
  if (isError) return <p>Invoices load failed</p>;
  return (
    <ul>
      {data?.map((i) => (
        <li key={i.id}>
          {i.invoiceNumber} — {i.totalAmount} — {i.status} — {i.source} — {i.currency} —{" "}
          {i.companyId} — {i.createdAt}
        </li>
      ))}
      {data?.length === 0 && <li>no invoices</li>}
    </ul>
  );
}

function MutationHarness({ companyId }: { companyId: string }) {
  const create = useCreateInvoice(companyId);
  const update = useUpdateInvoice(companyId);
  const remove = useDeleteInvoice(companyId);
  return (
    <div>
      <button onClick={() => create.mutate({ document_id: DOC_A, invoice_number: "NEW-1" })}>
        create
      </button>
      <button onClick={() => update.mutate({ id: INVOICE.id, body: { invoice_number: "EDITED" } })}>
        update
      </button>
      <button onClick={() => remove.mutate(INVOICE.id)}>delete</button>
      {create.isError || update.isError || remove.isError ? <p>mutation failed</p> : null}
    </div>
  );
}

function MappingHarness({
  companyId,
  vendorId,
}: {
  companyId: string | undefined;
  vendorId: string | null;
}) {
  const confirm = useConfirmVendorMapping(companyId);
  return (
    <div>
      <button onClick={() => confirm.mutate({ id: INVOICE.id, vendorId })}>map vendor</button>
      {confirm.isError ? <p>mapping failed</p> : null}
    </div>
  );
}

function MatchHarness({
  companyId,
  invoiceId,
}: {
  companyId: string | undefined;
  invoiceId: string;
}) {
  const match = useVendorMatch(companyId, invoiceId, true);
  return (
    <div>
      {match.data?.candidates.map((candidate) => (
        <p key={candidate.id}>{candidate.name}</p>
      ))}
      {match.isError ? <p>match failed</p> : null}
    </div>
  );
}

function renderWith(queryClient: QueryClient, node: ReactNode) {
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => jsonResponse([INVOICE])),
  );
});
afterEach(() => vi.unstubAllGlobals());

describe("Invoice mapper", () => {
  it("maps snake_case onto the UI model", () => {
    const inv = toInvoice(INVOICE);

    expect(inv.id).toBe(INVOICE.id);
    expect(inv.companyId).toBe(COMPANY_A);
    expect(inv.documentId).toBe(DOC_A);
    expect(inv.invoiceNumber).toBe("INV-2026-001");
    expect(inv.invoiceDate).toBe("2026-03-15");
    expect(inv.previousVendorId).toBeNull();
    expect(inv.taxableAmount).toBe("1000.00");
    expect(inv.createdAt).toBe(INVOICE.created_at);
  });

  it("keeps money as decimal strings, never numbers", () => {
    const inv = toInvoice({ ...INVOICE, taxable_amount: "0.30" });

    // A JS number would round-trip as 0.3 and lose the money semantics.
    expect(typeof inv.taxableAmount).toBe("string");
    expect(inv.taxableAmount).toBe("0.30");
  });

  it("keeps null amounts null rather than turning them into zero", () => {
    const inv = toInvoice({ ...INVOICE, igst_amount: null });

    expect(inv.igstAmount).toBeNull();
  });

  it("surfaces the manual source honestly", () => {
    expect(toInvoice(INVOICE).source).toBe("MANUAL");
  });

  it("surfaces AI extraction as a distinct source", () => {
    const inv = toInvoice({ ...INVOICE, source: "AI", status: "NEEDS_REVIEW" });

    expect(inv.source).toBe("AI");
    expect(statusToUiLabel(inv.status)).toBe("Needs Review");
  });

  it("labels the processing and failure states", () => {
    expect(statusToUiLabel("PROCESSING")).toBe("Processing");
    expect(statusToUiLabel("FAILED")).toBe("Failed");
  });

  it("carries the vendor mapping state through the mapper", () => {
    const inv = toInvoice({
      ...INVOICE,
      suggested_vendor_id: "aaaaaaaa-0000-0000-0000-000000000009",
      vendor_mapping_status: "SUGGESTED",
      vendor_match_method: "NORMALIZED_NAME",
    });

    expect(inv.suggestedVendorId).toBe("aaaaaaaa-0000-0000-0000-000000000009");
    expect(inv.vendorMappingStatus).toBe("SUGGESTED");
    expect(inv.vendorMatchMethod).toBe("NORMALIZED_NAME");
  });

  it("maps ambiguous match candidates without losing grouping fields", () => {
    const match = toVendorMatch({
      status: "AMBIGUOUS",
      reason: "MULTIPLE_NAME_MATCHES",
      method: null,
      vendor_id: null,
      requires_confirmation: false,
      candidates: [
        {
          id: "vendor-a",
          name: "ABC Traders",
          gstin: "27AABCT1234F1Z5",
          pan: "AABCT1234F",
          parent_group: "Raw Material",
          default_expense_ledger: "Purchase - Raw Material",
          creditor_ledger: "ABC Traders",
        },
      ],
    });

    expect(match.status).toBe("AMBIGUOUS");
    expect(match.requiresConfirmation).toBe(false);
    expect(match.candidates[0]).toEqual({
      id: "vendor-a",
      name: "ABC Traders",
      gstin: "27AABCT1234F1Z5",
      pan: "AABCT1234F",
      parentGroup: "Raw Material",
      defaultExpenseLedger: "Purchase - Raw Material",
      creditorLedger: "ABC Traders",
    });
  });

  it("explains deterministic mapping evidence without inventing confidence", () => {
    expect(matchMethodLabel("EXACT_GSTIN")).toBe("Matched by GSTIN");
    expect(matchMethodLabel("EXACT_PAN")).toBe("Matched by PAN");
    expect(matchMethodLabel("NORMALIZED_NAME")).toBe("Matched by normalized name");
    expect(matchMethodLabel(null)).toBe("Confirmed manually");
    expect(mappingStatusLabel("AMBIGUOUS")).toBe("Needs review");
    expect(mappingSummaryLabel("MATCHED", "EXACT_GSTIN")).toBe("Matched by GSTIN");
    expect(mappingSummaryLabel("MATCHED", "NORMALIZED_NAME")).toBe(
      "Matched from confirmed history",
    );
    expect(mappingSummaryLabel("SUGGESTED", "NORMALIZED_NAME")).toBe(
      "Suggested by normalized name",
    );
    expect(mappingSummaryLabel("SUGGESTED", null)).toBe("New vendor created from this invoice");
    expect(mappingSummaryLabel("UNMAPPED", null)).toBe("No vendor match");
  });

  it("blocks data approval while a vendor mapping still needs review", () => {
    const pending = toInvoice({
      ...INVOICE,
      status: "NEEDS_REVIEW",
      vendor_mapping_status: "SUGGESTED",
    });
    const ambiguous = toInvoice({
      ...INVOICE,
      status: "NEEDS_REVIEW",
      vendor_mapping_status: "AMBIGUOUS",
    });
    const confirmed = toInvoice({
      ...INVOICE,
      status: "NEEDS_REVIEW",
      vendor_mapping_status: "CONFIRMED",
    });
    const processed = toInvoice({
      ...INVOICE,
      status: "PROCESSED",
      vendor_mapping_status: "CONFIRMED",
    });

    expect(mappingNeedsReview("SUGGESTED")).toBe(true);
    expect(mappingNeedsReview("AMBIGUOUS")).toBe(true);
    expect(mappingNeedsReview("CONFIRMED")).toBe(false);
    expect(canApproveData(pending)).toBe(false);
    expect(canApproveData(ambiguous)).toBe(false);
    expect(canApproveData(confirmed)).toBe(true);
    expect(canApproveData(processed)).toBe(false);
  });

  it("builds an editable form with blank strings for missing values", () => {
    const form = invoiceToForm(toInvoice({ ...INVOICE, po_number: null, due_date: null }));

    expect(form.poNumber).toBe("");
    expect(form.dueDate).toBe("");
    expect(form.invoiceNumber).toBe("INV-2026-001");
  });

  it("converts the edit form back into a snake_case payload", () => {
    const payload = toInvoicePayload({
      invoiceNumber: "INV-9",
      taxableAmount: "1180.50",
      totalAmount: "1180.50",
    });

    expect(payload).toEqual({
      invoice_number: "INV-9",
      taxable_amount: "1180.50",
      total_amount: "1180.50",
    });
  });

  it("sends only the fields the form actually changed", () => {
    const payload = toInvoicePayload({ invoiceNumber: "INV-9" });

    expect(Object.keys(payload)).toEqual(["invoice_number"]);
  });

  it("turns blank form values into null", () => {
    const payload = toInvoicePayload({ poNumber: "   ", vendorName: "" });

    expect(payload.po_number).toBeNull();
    expect(payload.vendor_name).toBeNull();
  });

  it("round-trips an invoice through the form without losing money", () => {
    const form = invoiceToForm(toInvoice(INVOICE));
    const payload = toInvoicePayload(form);

    expect(payload.total_amount).toBe("1180.00");
    expect(payload.cgst_amount).toBe("90.00");
  });

  it("renders a status label for the badge", () => {
    expect(statusToUiLabel("NEEDS_REVIEW")).toBe("Needs Review");
    expect(statusToUiLabel("DRAFT")).toBe("Draft");
    expect(statusToUiLabel("PROCESSED")).toBe("Processed");
  });
});

describe("Invoice queries are company-scoped", () => {
  it("requests the company's own invoice path", async () => {
    renderWith(makeClient(), <ListHarness companyId={COMPANY_A} />);
    await waitFor(() => expect(screen.getByText(/INV-2026-001/)).toBeTruthy());

    expect(calls()[0][0]).toContain(`/api/v1/companies/${COMPANY_A}/invoices`);
  });

  it("renders the stored values with no invented data", async () => {
    renderWith(makeClient(), <ListHarness companyId={COMPANY_A} />);
    await waitFor(() => expect(screen.getByText(/INV-2026-001/)).toBeTruthy());

    const row = screen.getByText(/INV-2026-001/).textContent ?? "";
    expect(row).toContain("1180.00");
    expect(row).toContain("DRAFT");
    expect(row).toContain("MANUAL");
  });

  it("shows a loading state before invoices arrive", () => {
    renderWith(makeClient(), <ListHarness companyId={COMPANY_A} />);

    expect(screen.getByText("Loading invoices")).toBeTruthy();
  });

  it("shows an empty state rather than mock invoices", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([])),
    );
    renderWith(makeClient(), <ListHarness companyId={COMPANY_A} />);

    await waitFor(() => expect(screen.getByText("no invoices")).toBeTruthy());
  });

  it("shows an error rather than falling back to mock invoices", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "Boom" }, 500)),
    );
    renderWith(makeClient(), <ListHarness companyId={COMPANY_A} />);

    await waitFor(() => expect(screen.getByText("Invoices load failed")).toBeTruthy());
    expect(screen.queryByText(/INV-2026-001/)).toBeNull();
  });

  it("does not call the API without a company id", async () => {
    renderWith(makeClient(), <ListHarness companyId={undefined} />);

    await waitFor(() => expect(screen.getByText("Loading invoices")).toBeTruthy());
    expect(calls()).toHaveLength(0);
  });

  it("keeps each company's invoice cache separate", async () => {
    const shared = makeClient();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        jsonResponse(
          String(url).includes(COMPANY_A)
            ? [INVOICE]
            : [{ ...INVOICE, id: "bbb", invoice_number: "B-1", company_id: COMPANY_B }],
        ),
      ),
    );

    const first = renderWith(shared, <ListHarness companyId={COMPANY_A} />);
    await waitFor(() => expect(screen.getByText(/INV-2026-001/)).toBeTruthy());
    first.unmount();

    renderWith(shared, <ListHarness companyId={COMPANY_B} />);
    await waitFor(() => expect(screen.getByText(/B-1/)).toBeTruthy());
    expect(screen.queryByText(/INV-2026-001/)).toBeNull();
  });
});

describe("Invoice mutations", () => {
  it("POSTs a new invoice to the company path", async () => {
    renderWith(makeClient(), <MutationHarness companyId={COMPANY_A} />);
    fireEvent.click(screen.getByText("create"));

    await waitFor(() => {
      const post = calls().find(([, init]) => init?.method === "POST");
      expect(post).toBeTruthy();
      expect(post![0]).toContain(`/api/v1/companies/${COMPANY_A}/invoices`);
      expect(JSON.parse(String(post![1].body))).toEqual({
        document_id: DOC_A,
        invoice_number: "NEW-1",
      });
    });
  });

  it("PATCHes only the changed fields", async () => {
    renderWith(makeClient(), <MutationHarness companyId={COMPANY_A} />);
    fireEvent.click(screen.getByText("update"));

    await waitFor(() => {
      const patch = calls().find(([, init]) => init?.method === "PATCH");
      expect(patch).toBeTruthy();
      expect(JSON.parse(String(patch![1].body))).toEqual({ invoice_number: "EDITED" });
    });
  });

  it("DELETEs the invoice and leaves the document alone", async () => {
    renderWith(makeClient(), <MutationHarness companyId={COMPANY_A} />);
    fireEvent.click(screen.getByText("delete"));

    await waitFor(() => {
      const del = calls().find(([url, init]) => init?.method === "DELETE");
      expect(del).toBeTruthy();
      // Deleting an invoice must not touch the document endpoint.
      expect(del![0]).toContain(`/api/v1/companies/${COMPANY_A}/invoices/${INVOICE.id}`);
      expect(del![0]).not.toContain("/documents");
    });
  });

  it("surfaces a rejected status transition", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) =>
        init?.method === "PATCH"
          ? jsonResponse({ detail: "Cannot move an invoice from DRAFT to PROCESSED." }, 409)
          : jsonResponse([INVOICE]),
      ),
    );
    renderWith(makeClient(), <MutationHarness companyId={COMPANY_A} />);
    fireEvent.click(screen.getByText("update"));

    await waitFor(() => expect(screen.getByText("mutation failed")).toBeTruthy());
  });

  it("POSTs a vendor confirmation to the company-scoped invoice path", async () => {
    renderWith(makeClient(), <MappingHarness companyId={COMPANY_A} vendorId="vendor-a" />);
    fireEvent.click(screen.getByText("map vendor"));

    await waitFor(() => {
      const post = calls().find(
        ([url, init]) => init?.method === "POST" && String(url).includes("/vendor-mapping"),
      );
      expect(post).toBeTruthy();
      expect(post![0]).toContain(
        `/api/v1/companies/${COMPANY_A}/invoices/${INVOICE.id}/vendor-mapping`,
      );
      expect(JSON.parse(String(post![1].body))).toEqual({ vendor_id: "vendor-a" });
    });
  });

  it("sends null to reject a suggestion without assigning a vendor", async () => {
    renderWith(makeClient(), <MappingHarness companyId={COMPANY_A} vendorId={null} />);
    fireEvent.click(screen.getByText("map vendor"));

    await waitFor(() => {
      const post = calls().find(
        ([url, init]) => init?.method === "POST" && String(url).includes("/vendor-mapping"),
      );
      expect(JSON.parse(String(post![1].body))).toEqual({ vendor_id: null });
    });
  });

  it("surfaces a vendor-mapping failure instead of pretending it succeeded", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "Vendor not found" }, 404)),
    );
    renderWith(makeClient(), <MappingHarness companyId={COMPANY_A} vendorId="vendor-b" />);
    fireEvent.click(screen.getByText("map vendor"));

    await waitFor(() => expect(screen.getByText("mapping failed")).toBeTruthy());
  });

  it("fetches the company-scoped match preview candidates", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        String(url).includes("/vendor-match")
          ? jsonResponse({
              status: "AMBIGUOUS",
              reason: "MULTIPLE_NAME_MATCHES",
              method: null,
              vendor_id: null,
              requires_confirmation: false,
              candidates: [
                {
                  id: "vendor-a",
                  name: "ABC Traders",
                  gstin: "27AABCT1234F1Z5",
                  pan: "AABCT1234F",
                  parent_group: "Raw Material",
                  default_expense_ledger: "Purchase - Raw Material",
                  creditor_ledger: "ABC Traders",
                },
              ],
            })
          : jsonResponse([INVOICE]),
      ),
    );

    renderWith(makeClient(), <MatchHarness companyId={COMPANY_A} invoiceId={INVOICE.id} />);

    await waitFor(() => expect(screen.getByText("ABC Traders")).toBeTruthy());
    const get = calls().find(([url]) => String(url).includes("/vendor-match"));
    expect(get![0]).toContain(`/api/v1/companies/${COMPANY_A}/invoices/${INVOICE.id}/vendor-match`);
  });

  it("surfaces a match-preview failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "Invoice not found" }, 404)),
    );
    renderWith(makeClient(), <MatchHarness companyId={COMPANY_A} invoiceId={INVOICE.id} />);

    await waitFor(() => expect(screen.getByText("match failed")).toBeTruthy());
  });
});
