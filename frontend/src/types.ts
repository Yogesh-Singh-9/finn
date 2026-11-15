// Mirrors backend/finn/models.py (BriefSnapshot and friends). Money in ₹ crore,
// percentages as fractions, dates as ISO strings.

export type Tier = "needs_you" | "glance" | "set_aside";

export interface ScorePart {
  part: "category" | "subtype" | "uncertain" | "amount" | "numbers" | "guidance" | "reaction" | "thesis";
  points: number;
  note: string;
}

export interface Reaction {
  session: string;
  ret: number;
  abn_ret: number;
  z: number | null;
  vol_x: number | null;
}

export interface FilingRef {
  id: string;
  nse_subject: string;
  title: string;
  category: string;
  subtype: string | null;
  classified_by: string;
  filed_at: string;
  url: string | null;
}

export interface Metric {
  key: string;
  label: string;
  value: number | null;
  prev_q: number | null;
  prev_y: number | null;
  qoq: number | null;
  yoy: number | null;
  source: string | null;
  note: string | null;
}

export interface Margin {
  key: string;
  label: string;
  value: number | null;
  prev_q: number | null;
  prev_y: number | null;
  qoq_bps: number | null;
  yoy_bps: number | null;
}

export interface Check {
  name: string;
  expected: number;
  actual: number;
  tolerance: number;
  ok: boolean;
}

export interface OneOff {
  description: string;
  amount_cr: number;
  affects: "profit" | "revenue" | "both" | "unclear";
  effect: "gain" | "charge" | "unclear";
  quote: string;
  source: string;
  verified: boolean;
}

export interface AdjustedMetric {
  key: string;
  label: string;
  value: number;
  prev_y: number | null;
  prev_y_source: "company" | "screener";
  yoy: number | null;
  quote: string;
  source: string;
}

export interface GuidedDelivered {
  metric: string;
  guided: string;
  delivered: string;
  grade: Grade;
  source: Record<string, string>;
  call_label?: string | null;
  promise_id?: string | null;
  progress_flag?: "on_track" | "behind" | null;
  note?: string | null;
}

export interface ResultsCard {
  symbol: string;
  period_key: string;
  period_label: string;
  basis: "consolidated" | "standalone";
  layout: "standard" | "bank";
  source: "screener" | "NSE XBRL";
  source_note: string;
  headline: string;
  metrics: Metric[];
  margins: Margin[];
  checks: Check[];
  verified: boolean;
  status: string;
  filing_url: string | null;
  xbrl_url: string | null;
  guidance: GuidedDelivered[];
  guidance_note: string;
  one_offs: OneOff[];
  adjusted: AdjustedMetric[];
  reported_headline: string | null;
  one_offs_by: string | null;
}

export interface PriorRole {
  org: string;
  role: string | null;
  quote: string;
}

export interface PersonChange {
  name: string;
  role: string | null;
  change: "appointed" | "resigned" | "retired" | "ceased" | "re-appointed" | "redesignated" | "other";
  effective: string | null;
  reason: string | null;
  background: string | null;
  prior_roles: PriorRole[];
  dropped: string[];
  linkedin_search: string;
  source: Record<string, string>;
}

export interface MgmtCard {
  symbol: string;
  headline: string;
  short: string | null;
  people: PersonChange[];
  extracted_by: string;
  filing_urls: string[];
}

export interface BriefEvent {
  id: string;
  symbol: string;
  company: string;
  category: string;
  subtype: string | null;
  title: string;
  occurred_at: string;
  filings: FilingRef[];
  people: string[];
  amount_cr: number | null;
  reaction: Reaction | null;
  reaction_pending: boolean;
  score: number;
  breakdown: ScorePart[];
  tier: Tier;
  summary: string | null;
  summary_by: string | null;
  results_card: ResultsCard | null;
  mgmt_card: MgmtCard | null;
}

export interface Move {
  symbol: string;
  session: string;
  close: number;
  ret: number;
  abn_ret: number;
  z: number | null;
  vol_x: number | null;
  event_id: string | null;
  spark: number[];
}

export interface LadderStats {
  filings: number;
  by_step: Record<string, number>;
  pdfs_read: number;
  llm_calls: number;
  llm_filings: number;
  never_touched_model: number;
  never_touched_model_pct: number;
  llm_status: string;
}

export interface BriefSnapshot {
  generated_at: string;
  window_from: string;
  window_to: string;
  prices_as_of: string | null;
  symbols: string[];
  events: BriefEvent[];
  tiers: Record<string, number>;
  ladder: LadderStats;
  cards: Record<string, number>;
  llm_usage: string | null;
  moves: Move[];
}

/* ---------------- phase 6: promise ledger + research dossier ---------------- */
export type Grade = "exceeded" | "met" | "missed" | "pending" | "revised" | "not_comparable";

export interface Cite {
  label: string;
  doc_id: string | null;
  page: number | null;
  quote: string | null;
}

export interface LedgerPromise {
  id: string;
  symbol: string;
  call_quarter: string;
  call_date: string;
  call_label: string;
  speaker: string | null;
  kind: "numeric" | "qualitative";
  metric: string;
  metric_key: string;
  segment: string | null;
  target_text: string | null;
  low: number | null;
  high: number | null;
  unit: string | null;
  qualifier: string;
  horizon_text: string | null;
  horizon: string | null;
  horizon_kind: "quarter" | "half" | "year" | "multi_year" | "unspecified";
  source: Cite;
  grade: Grade;
  grade_note: string;
  actual: number | null;
  actual_text: string | null;
  actual_source: Cite | null;
  progress: string | null;
  progress_flag: "on_track" | "behind" | null;
  revised_from: string[];
  revised_by: string | null;
  revision_dir: "up" | "down" | "same" | null;
  judged_by: string;
}

export interface Ledger {
  symbol: string;
  built_at: string;
  calls: Cite[];
  promises: LedgerPromise[];
  stats: Record<string, number>;
  notes: string[];
  llm: string | null;
}

export interface Vital {
  key: string;
  label: string;
  value: string;
  raw: number | null;
  off: boolean;
  note: string;
  source: Cite;
}

export interface Point {
  text: string;
  cites: Cite[];
  flagged: boolean;
}

export interface Section {
  key: string;
  title: string;
  conclusion: string;
  rows: Record<string, any>[];
  series: Record<string, any>[];
  notes: string[];
}

export interface ResearchSnapshot {
  symbol: string;
  company: string;
  built_at: string;
  price: number | null;
  price_date: string | null;
  day_change: number | null;
  vitals: Vital[];
  verdict: Point[];
  sections: Section[];
  ledger: Ledger;
  bull: Point[];
  bear: Point[];
  questions: Point[];
  sources: Cite[];
  cost_line: string | null;
  notes: string[];
}
