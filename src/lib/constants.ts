import type { TDSSection } from "./types";

/** Calendar months in the order an Indian financial year reports them. */
export const PERIODS: string[] = [
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
  "January",
  "February",
  "March",
];

const fyStartYear = (date: Date) =>
  date.getMonth() >= 3 ? date.getFullYear() : date.getFullYear() - 1;

const fyLabel = (startYear: number) =>
  `FY ${startYear}-${String((startYear + 1) % 100).padStart(2, "0")}`;

/** The current financial year followed by the two previous ones. */
export const FINANCIAL_YEARS = Array.from({ length: 3 }, (_, index) =>
  fyLabel(fyStartYear(new Date()) - index),
);

/** Current reporting month in the April-to-March sequence. */
export const currentPeriod = (date = new Date()) => PERIODS[(date.getMonth() + 9) % 12];

/** Statutory TDS sections tracked by the working. */
export const TDS_SECTIONS: TDSSection[] = ["194C", "194J", "194I", "194H", "194Q"];

/** Reference rates used by the working; the backend remains the source of truth. */
export const tdsRates: Record<TDSSection, number> = {
  "194C": 2,
  "194J": 10,
  "194I": 10,
  "194H": 5,
  "194Q": 0.1,
};
