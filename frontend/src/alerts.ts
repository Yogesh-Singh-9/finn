/* Alert settings: one place, read by Holdings (sliders + back-test) and Today (moves strip).
 * Per viewer, in localStorage; every access is wrapped so private mode just uses defaults. */

export function read<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v ? (JSON.parse(v) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function write(key: string, v: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(v));
  } catch {
    /* private mode: settings just won't persist */
  }
}

export type Alerts = { price: number; volume: number; cats: string[] };
export const ALERTS_KEY = "finn-alerts";
export const DEFAULT_ALERTS: Alerts = {
  price: 3, volume: 2.5,
  cats: ["results", "management_change", "M&A", "fund_raise", "credit_rating", "litigation", "pledging", "capex"],
};
export const ALERT_CATS = ["results", "management_change", "M&A", "fund_raise", "credit_rating", "litigation", "pledging", "capex",
  "dividend", "bonus_split", "insider_trading", "investor_meetings", "earnings_call", "press_release", "agm_egm", "postal_ballot"];

export const readAlerts = (): Alerts => ({ ...DEFAULT_ALERTS, ...read<Partial<Alerts>>(ALERTS_KEY, {}) });

/** The one rule, shared by the back-test and the Today strip: |day move| ≥ X% or volume ≥ N× its 20-day average. */
export function trips(ret: number, volX: number | null | undefined, a: Alerts): boolean {
  return Math.abs(ret) * 100 >= a.price || (volX ?? 0) >= a.volume;
}
