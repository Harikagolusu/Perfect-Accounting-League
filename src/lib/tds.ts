import type { TDSWorking } from "./types";

/** Reference TDS due = base × rate, rounded to the rupee. Not tax advice. */
export const tdsDue = (t: Pick<TDSWorking, "base" | "rate">) => Math.round((t.base * t.rate) / 100);
export const tdsVariance = (t: Pick<TDSWorking, "base" | "rate" | "deducted">) => tdsDue(t) - t.deducted;
export const quarterOf = (iso: string) => {
  const m = Number(iso.slice(5, 7));
  return m >= 4 && m <= 6 ? "Q1" : m <= 9 && m >= 7 ? "Q2" : m >= 10 ? "Q3" : "Q4";
};
