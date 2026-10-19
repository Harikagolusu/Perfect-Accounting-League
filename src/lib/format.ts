const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const num = new Intl.NumberFormat("en-IN");

export const fmtINR = (v: number | null | undefined) => (v == null ? "—" : inr.format(v));
export const fmtNum = (v: number) => num.format(v);
export const fmtDate = (iso: string) => {
  const [y, m, d] = iso.slice(0, 10).split("-");
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${d} ${months[Number(m) - 1]} ${y}`;
};
export const fmtSize = (bytes: number) =>
  bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`;

/** Masks PAN / GSTIN / account numbers to avoid unnecessary exposure. */
export const mask = (v: string, visible = 4) =>
  !v ? "—" : v.length <= visible ? v : `${"•".repeat(Math.max(0, v.length - visible))}${v.slice(-visible)}`;

export const GSTIN_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;
export const PAN_RE = /^[A-Z]{5}[0-9]{4}[A-Z]$/;
export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
