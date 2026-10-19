import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  statusToApi,
  statusToUi,
  toVendor,
  toVendorPayload,
  type VendorDto,
} from "@/lib/api/vendors";
import { useVendors } from "@/lib/queries/vendors";
import type { Vendor } from "@/lib/types";

const COMPANY_A = "11111111-1111-1111-1111-111111111111";
const COMPANY_B = "22222222-2222-2222-2222-222222222222";

const A1: VendorDto = {
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  company_id: COMPANY_A,
  name: "Vendor A1",
  gstin: "27AAJCS4821K1Z5",
  pan: "AAJCS4821K",
  default_expense_ledger: "Freight Inward",
  creditor_ledger: "Vendor A1",
  parent_group: "Indirect Expenses",
  status: "ACTIVE",
  created_at: "2026-10-19T06:00:00Z",
  updated_at: "2026-10-19T06:00:00Z",
};

const B1: VendorDto = {
  ...A1,
  id: "bbbbbbbb-0000-0000-0000-000000000001",
  company_id: COMPANY_B,
  name: "Vendor B1",
};

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

const calls = () => vi.mocked(fetch).mock.calls as unknown as [string, RequestInit][];

function Harness({ companyId }: { companyId: string | undefined }) {
  const { data, isPending, isError } = useVendors(companyId);
  if (isPending) return <p>Loading vendors</p>;
  if (isError) return <p>Vendor load failed</p>;
  return (
    <ul>
      {data?.map((v) => (
        <li key={v.id}>
          {v.name} — {v.companyId} — {v.status}
        </li>
      ))}
      {data?.length === 0 && <li>none</li>}
    </ul>
  );
}

function renderHarness(companyId: string | undefined) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Harness companyId={companyId} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => jsonResponse([A1])),
  );
});
afterEach(() => vi.unstubAllGlobals());

describe("Vendor mapper", () => {
  it("maps snake_case onto the UI model", () => {
    const vendor = toVendor(A1);

    expect(vendor.id).toBe(A1.id);
    expect(vendor.companyId).toBe(COMPANY_A);
    expect(vendor.name).toBe("Vendor A1");
    expect(vendor.defaultExpenseLedger).toBe("Freight Inward");
    expect(vendor.creditorLedger).toBe("Vendor A1");
    expect(vendor.parentGroup).toBe("Indirect Expenses");
  });

  it("carries the backend company_id through unchanged", () => {
    expect(toVendor(A1).companyId).toBe(COMPANY_A);
    expect(toVendor(B1).companyId).toBe(COMPANY_B);
  });

  it("maps every backend status onto a UI status", () => {
    expect(statusToUi("ACTIVE")).toBe("Active");
    expect(statusToUi("INACTIVE")).toBe("Inactive");
    expect(statusToUi("NEEDS_MAPPING")).toBe("Needs Mapping");
  });

  it("maps UI statuses back onto the API enum", () => {
    expect(statusToApi("Active")).toBe("ACTIVE");
    expect(statusToApi("Inactive")).toBe("INACTIVE");
    expect(statusToApi("Needs Mapping")).toBe("NEEDS_MAPPING");
  });

  it("reports zero transactions rather than inventing an aggregate", () => {
    const vendor = toVendor(A1);
    expect(vendor.transactions).toBe(0);
    expect(vendor.lastUsed).toBe("");
  });

  it("coerces null columns to empty strings", () => {
    const vendor = toVendor({
      ...A1,
      gstin: null,
      pan: null,
      default_expense_ledger: null,
      creditor_ledger: null,
      parent_group: null,
    });

    expect(vendor.gstin).toBe("");
    expect(vendor.defaultExpenseLedger).toBe("");
    expect(vendor.parentGroup).toBe("");
  });

  it("never sends company_id in the body; it is path-owned", () => {
    const payload = toVendorPayload(toVendor(A1));

    expect(Object.keys(payload).sort()).toEqual(
      [
        "creditor_ledger",
        "default_expense_ledger",
        "gstin",
        "name",
        "pan",
        "parent_group",
        "status",
      ].sort(),
    );
    expect("company_id" in payload).toBe(false);
  });

  it("converts blanks to null on the way out", () => {
    const payload = toVendorPayload(toVendor({ ...A1, pan: null, parent_group: "  " }));
    expect(payload.pan).toBeNull();
    expect(payload.parent_group).toBeNull();
  });
});

describe("Vendor queries are company-scoped", () => {
  it("requests the company's own vendor path", async () => {
    renderHarness(COMPANY_A);
    await waitFor(() => expect(screen.getByText(/Vendor A1/)).toBeTruthy());

    const [url] = calls()[0];
    expect(url).toContain(`/api/v1/companies/${COMPANY_A}/vendors`);
  });

  it("renders only the vendors the API returned for that company", async () => {
    renderHarness(COMPANY_A);
    await waitFor(() => expect(screen.getByText(/Vendor A1/)).toBeTruthy());

    const row = screen.getByText(/Vendor A1/).textContent ?? "";
    expect(row).toContain(COMPANY_A);
    expect(row).not.toContain("Vendor B1");
  });

  it("does not call the API at all without a company id", async () => {
    renderHarness(undefined);
    await waitFor(() => expect(screen.getByText("Loading vendors")).toBeTruthy());
    expect(calls()).toHaveLength(0);
  });

  it("surfaces an error rather than falling back to mock vendors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "Boom" }, 500)),
    );

    renderHarness(COMPANY_A);

    await waitFor(() => expect(screen.getByText("Vendor load failed")).toBeTruthy());
    // Crucially: no mock vendor rows are substituted for the failure.
    expect(screen.queryByText(/Vendor A1/)).toBeNull();
    expect(screen.queryByText(/Vendor B1/)).toBeNull();
  });

  it("handles an empty vendor response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([])),
    );
    renderHarness(COMPANY_A);

    await waitFor(() => expect(screen.getByText("none")).toBeTruthy());
  });

  it("keeps each company's cache separate", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => jsonResponse(String(url).includes(COMPANY_A) ? [A1] : [B1])),
    );

    const { unmount } = render(
      <QueryClientProvider client={client}>
        <Harness companyId={COMPANY_A} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText(/Vendor A1/)).toBeTruthy());
    unmount();

    render(
      <QueryClientProvider client={client}>
        <Harness companyId={COMPANY_B} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText(/Vendor B1/)).toBeTruthy());
    expect(screen.queryByText(/Vendor A1/)).toBeNull();
  });
});

describe("Vendor payload round-trip", () => {
  it("round-trips without losing stored data", () => {
    expect(toVendorPayload(toVendor(A1))).toEqual({
      name: "Vendor A1",
      gstin: "27AAJCS4821K1Z5",
      pan: "AAJCS4821K",
      default_expense_ledger: "Freight Inward",
      creditor_ledger: "Vendor A1",
      parent_group: "Indirect Expenses",
      status: "ACTIVE",
    });
  });

  it("preserves a Needs Mapping status through the round trip", () => {
    const vendor: Vendor = toVendor({ ...A1, status: "NEEDS_MAPPING" });
    expect(toVendorPayload(vendor).status).toBe("NEEDS_MAPPING");
  });
});
