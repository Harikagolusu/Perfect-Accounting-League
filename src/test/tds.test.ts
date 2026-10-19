import { describe, expect, it } from "vitest";
import { tdsDue, tdsVariance } from "@/lib/tds";

describe("TDS calculation", () => {
  it("computes due as base x rate", () => {
    expect(tdsDue({ base: 100000, rate: 2 })).toBe(2000);
  });
  it("variance is due minus deducted", () => {
    expect(tdsVariance({ base: 100000, rate: 10, deducted: 4000 })).toBe(6000);
  });
});
