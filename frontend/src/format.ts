// Formatting at the edge only: the data keeps ₹ crore floats and fractions.
const inr0 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const inr1 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1, minimumFractionDigits: 1 });

export const MINUS = "−"; // typographic minus, same width as + in tabular mono

export function cr(v: number | null | undefined): string {
  if (v == null) return "—";
  const s = Math.abs(v) < 100 ? inr1.format(Math.abs(v)) : inr0.format(Math.abs(v));
  return (v < 0 ? MINUS : "") + s;
}

export function pct(v: number | null | undefined, signed = true): string {
  if (v == null) return "—";
  const a = Math.abs(v * 100);
  const body = a >= 100 ? a.toFixed(0) : a >= 10 ? a.toFixed(0) : a.toFixed(1);
  const sign = !signed ? (v < 0 ? MINUS : "") : v > 0 ? "+" : v < 0 ? MINUS : "";
  return `${sign}${body}%`;
}

export function bps(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : v < 0 ? MINUS : ""}${Math.abs(Math.round(v))}`;
}

export function sigma(z: number | null | undefined): string {
  if (z == null) return "—";
  return `${z > 0 ? "+" : z < 0 ? MINUS : ""}${Math.abs(z).toFixed(1)}σ`;
}

export function tone(v: number | null | undefined): "up" | "down" | "flat" {
  if (v == null || Math.abs(v) < 1e-9) return "flat";
  return v > 0 ? "up" : "down";
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** ISO "2026-11-05T16:50:28" -> "05 Nov" / "05 Nov 16:50". Read as IST wall time,
 *  which is how NSE stamps filings; no timezone conversion. */
export function day(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-");
  void y;
  return `${d} ${MONTHS[Number(m) - 1]}`;
}

export function dayTime(iso: string): string {
  return `${day(iso)} ${iso.slice(11, 16)}`;
}

export function wall(iso: string): number {
  // milliseconds for comparisons, treating the wall time as UTC (consistent everywhere)
  return Date.parse(iso.length <= 10 ? `${iso}T00:00:00Z` : `${iso.slice(0, 19)}Z`);
}

export function label(s: string | null | undefined): string {
  return (s ?? "").replace(/_/g, " ");
}

export function host(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
