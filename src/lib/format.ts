const inr = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});
const num = new Intl.NumberFormat("en-IN");

export const fmtINR = (v: number | null | undefined) => (v == null ? "—" : inr.format(v));
export const fmtNum = (v: number) => num.format(v);
export const fmtDate = (iso: string) => {
  const [y, m, d] = iso.slice(0, 10).split("-");
  const months = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ];
  return `${d} ${months[Number(m) - 1]} ${y}`;
};
export const fmtSize = (bytes: number) =>
  bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`;

/**
 * Groups an already-numeric string in Indian style (12,34,567.89).
 *
 * Financial amounts arrive from the backend as decimal strings and must stay
 * strings, so they are never routed through `Intl` or `Number()`: either would
 * reinterpret the value as binary floating point. Only the separators are
 * inserted, digit for digit.
 */
const groupIndianDigits = (digits: string): string => {
  if (digits.length <= 3) return digits;
  const tail = digits.slice(-3);
  const head = digits.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  return `${head},${tail}`;
};

/** Formats a decimal money string for display without converting it. */
export const fmtMoney = (value: string | null | undefined): string => {
  if (value === null || value === undefined || value === "") return "—";
  const negative = value.startsWith("-");
  const [whole = "", fraction] = (negative ? value.slice(1) : value).split(".");
  const grouped = groupIndianDigits(whole || "0");
  return `${negative ? "-" : ""}${fraction === undefined ? grouped : `${grouped}.${fraction}`}`;
};

/**
 * Compares two decimal money strings.
 *
 * Plain string comparison would rank "100.00" below "9.00", and numeric
 * comparison would reintroduce the float this whole layer exists to avoid, so
 * magnitude is compared digit by digit instead.
 */
export const compareMoney = (
  a: string | null | undefined,
  b: string | null | undefined,
): number => {
  if (a === b) return 0;
  if (a === null || a === undefined || a === "")
    return b === null || b === undefined || b === "" ? 0 : -1;
  if (b === null || b === undefined || b === "") return 1;

  const aNeg = a.startsWith("-");
  const bNeg = b.startsWith("-");
  if (aNeg !== bNeg) return aNeg ? -1 : 1;

  const split = (v: string) => {
    const [whole = "0", fraction = ""] = (v.startsWith("-") ? v.slice(1) : v).split(".");
    return { whole: whole.replace(/^0+(?=\d)/, ""), fraction };
  };

  const left = split(a);
  const right = split(b);
  let magnitude = 0;
  if (left.whole.length !== right.whole.length) {
    magnitude = left.whole.length < right.whole.length ? -1 : 1;
  } else if (left.whole !== right.whole) {
    magnitude = left.whole < right.whole ? -1 : 1;
  } else {
    const width = Math.max(left.fraction.length, right.fraction.length);
    const lf = left.fraction.padEnd(width, "0");
    const rf = right.fraction.padEnd(width, "0");
    magnitude = lf === rf ? 0 : lf < rf ? -1 : 1;
  }

  // Negatives invert the ordering, but equality stays equality.
  return aNeg ? -magnitude : magnitude;
};

/** Masks PAN / GSTIN / account numbers to avoid unnecessary exposure. */
export const mask = (v: string, visible = 4) =>
  !v
    ? "—"
    : v.length <= visible
      ? v
      : `${"•".repeat(Math.max(0, v.length - visible))}${v.slice(-visible)}`;

export const GSTIN_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;
export const PAN_RE = /^[A-Z]{5}[0-9]{4}[A-Z]$/;
export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
