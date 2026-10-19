import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CompanyDto } from "@/lib/api/companies";
import { useCompanies, useCreateCompany, useDeactivateCompany } from "@/lib/queries/companies";
import type { Company } from "@/lib/types";

const DTO: CompanyDto = {
  id: "1f0c6d2a-2c3b-4a5d-9e8f-0a1b2c3d4e5f",
  name: "Sahyadri Agro",
  legal_name: "Sahyadri Agro Foods Private Limited",
  gstin: "27AAJCS4821K1Z5",
  pan: "AAJCS4821K",
  email: "accounts@sahyadri-demo.in",
  phone: null,
  address: null,
  financial_year: "FY 2026-27",
  status: "ONBOARDING",
  created_at: "2026-10-19T06:00:00Z",
  updated_at: "2026-10-19T06:00:00Z",
};

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

const BLANK: Company = {
  id: "",
  legalName: "",
  displayName: "",
  pan: "",
  gstin: "",
  address: "",
  state: "",
  contactPerson: "",
  email: "",
  phone: "",
  financialYear: "",
  software: "None",
  assignedUsers: [],
  status: "Active",
};

function Harness() {
  const { data, isPending, isError } = useCompanies(true);
  const create = useCreateCompany();
  const deactivate = useDeactivateCompany();

  if (isPending) return <p>Loading companies</p>;
  if (isError) return <p>Could not load companies</p>;

  return (
    <ul>
      {data?.map((c) => (
        <li key={c.id}>
          {c.displayName} — {c.status} — {c.state} — {c.id}
        </li>
      ))}
      <button onClick={() => create.mutate({ ...BLANK, displayName: "New Co" })}>create</button>
      <button onClick={() => deactivate.mutate(DTO.id)}>deactivate</button>
    </ul>
  );
}

function renderHarness() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  );
}

const calls = () => vi.mocked(fetch).mock.calls as unknown as [string, RequestInit][];

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => jsonResponse([DTO])),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Companies data layer", () => {
  it("renders companies returned by the API", async () => {
    renderHarness();
    expect(screen.getByText(/Loading companies/)).toBeTruthy();
    await waitFor(() => expect(screen.getByText(/Sahyadri Agro/)).toBeTruthy());
  });

  it("maps backend fields onto the UI model", async () => {
    renderHarness();
    await waitFor(() => expect(screen.getByText(/Sahyadri Agro/)).toBeTruthy());

    const row = screen.getByText(/Sahyadri Agro/).textContent ?? "";
    expect(row).toContain("Onboarding"); // ONBOARDING -> Onboarding
    expect(row).toContain("Maharashtra"); // state derived from the GSTIN
    expect(row).toContain(DTO.id); // backend UUID, not a mock id
  });

  it("requests the v1 endpoint with include_inactive", async () => {
    renderHarness();
    await waitFor(() => expect(screen.getByText(/Sahyadri Agro/)).toBeTruthy());

    const [url] = calls()[0];
    expect(url).toContain("/api/v1/companies");
    expect(url).toContain("include_inactive=true");
  });

  it("POSTs on create and refetches", async () => {
    renderHarness();
    await waitFor(() => expect(screen.getByText(/Sahyadri Agro/)).toBeTruthy());

    fireEvent.click(screen.getByText("create"));

    await waitFor(() => {
      const post = calls().find(
        ([url, init]) => url.endsWith("/companies") && init?.method === "POST",
      );
      expect(post).toBeTruthy();
      expect(JSON.parse(String(post![1].body)).name).toBe("New Co");
    });
    // Invalidation refetches the list.
    await waitFor(() => expect(calls().length).toBeGreaterThan(1));
  });

  it("DELETEs on deactivate", async () => {
    renderHarness();
    await waitFor(() => expect(screen.getByText(/Sahyadri Agro/)).toBeTruthy());

    fireEvent.click(screen.getByText("deactivate"));

    await waitFor(() => {
      const del = calls().find(([url, init]) => url.includes(DTO.id) && init?.method === "DELETE");
      expect(del).toBeTruthy();
    });
  });

  it("surfaces an error state when the API fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "Boom" }, 500)),
    );

    renderHarness();

    await waitFor(() => expect(screen.getByText(/Could not load companies/)).toBeTruthy());
  });
});
