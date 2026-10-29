import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { tallyApi } from "@/lib/api/tally";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("tallyApi", () => {
  it("returns the safe connectivity payload from the backend", async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        connected: true,
        url: "http://localhost:9000",
        message: "TallyPrime is reachable",
      }),
    } as Response);

    await expect(tallyApi.status()).resolves.toEqual({
      connected: true,
      url: "http://localhost:9000",
      message: "TallyPrime is reachable",
    });

    expect(fetch).toHaveBeenCalledOnce();
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain("/api/v1/tally/status");
  });

  it("posts a company sync request to the backend", async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        success: true,
        companies_fetched: 1,
        companies_created: 1,
        companies_updated: 0,
        companies_unchanged: 0,
        error_message: null,
      }),
    } as Response);

    await expect(tallyApi.syncCompanies()).resolves.toMatchObject({
      success: true,
      companies_created: 1,
    });

    expect(fetch).toHaveBeenCalledOnce();
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain("/api/v1/tally/companies/sync");
    expect((init as RequestInit).method).toBe("POST");
  });
});
