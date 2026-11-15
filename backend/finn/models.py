"""Pydantic models for normalised source data. These are the contract between
ingestion, snapshot builders and (mirrored in TS) the frontend.

Units: money in INR crore (float); percentages as fractions (0.142).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class SourceStatus(BaseModel):
    source: str
    symbol: str | None = None
    ok: bool
    error: str | None = None
    detail: str | None = None


# ---------------------------------------------------------------- NSE filings
class Filing(BaseModel):
    id: str                       # NSE seq_id
    symbol: str
    company: str
    nse_subject: str              # NSE's own category label ("desc"), e.g. "Change in Director(s)"
    text: str                     # NSE's one-line summary ("attchmntText")
    filed_at: datetime            # IST, naive
    attachment_url: str | None
    attachment_bytes: int | None = None
    has_xbrl: bool = False
    industry: str | None = None


class FilingsSnapshot(BaseModel):
    generated_at: datetime
    window_from: date
    window_to: date
    symbols: list[str]
    filings: list[Filing]
    status: list[SourceStatus]


# ------------------------------------------------------- NSE results (XBRL)
class ResultsFilingRef(BaseModel):
    symbol: str
    period_end: date
    period_key: str | None        # FY27Q2
    consolidated: bool
    audited: bool
    broadcast_at: datetime | None
    xbrl_url: str | None
    filing_type: str              # "Integrated Filing- Financials" | legacy
    seq_id: str


class XbrlResults(BaseModel):
    """Raw facts from one results XBRL. Only the current quarter (OneD) and the
    year-to-date (FourD) contexts exist in the file. No prior-quarter columns,
    so QoQ/YoY bases must come from screener."""
    symbol: str
    period_key: str | None
    period_start: date | None
    period_end: date | None
    consolidated: bool | None
    source_url: str
    quarter: dict[str, float] = Field(default_factory=dict)   # tag -> INR crore
    ytd: dict[str, float] = Field(default_factory=dict)


# -------------------------------------------------------------- EOD prices
class PriceSeries(BaseModel):
    symbol: str                   # NSE symbol (or ^NSEI)
    dates: list[date]
    close: list[float]
    volume: list[int]


class PricesSnapshot(BaseModel):
    generated_at: datetime
    series: list[PriceSeries]
    status: list[SourceStatus]


# ------------------------------------------------------- Screener fundamentals
class Table(BaseModel):
    """A screener table: columns are period keys, rows are label -> values.
    Percent rows are stored as fractions. Missing cells are None."""
    columns: list[str]            # FY27Q2 / FY26 / "TTM"
    column_labels: list[str]      # as shown: "Sep 2026"
    rows: dict[str, list[float | None]]


class ConcallDoc(BaseModel):
    period_label: str             # "Aug 2026" (call month, not quarter)
    transcript_url: str | None = None
    ppt_url: str | None = None
    recording_url: str | None = None


class AnnualReportDoc(BaseModel):
    year: int                     # "Annual Report 2026" -> 2026 (FY26)
    url: str


class Fundamentals(BaseModel):
    symbol: str
    company: str | None
    basis: Literal["consolidated", "standalone"]
    layout: Literal["standard", "bank"]   # banks/NBFCs: Revenue/Financing Profit, no EBITDA
    fetched_at: datetime
    source_url: str
    quarters: Table | None = None
    profit_loss: Table | None = None
    balance_sheet: Table | None = None
    cash_flow: Table | None = None
    ratios: Table | None = None
    concalls: list[ConcallDoc] = Field(default_factory=list)
    annual_reports: list[AnnualReportDoc] = Field(default_factory=list)


# -------------------------------------------------------- results calendar
class CalendarEvent(BaseModel):
    symbol: str
    company: str
    purpose: str
    description: str
    date: date


# ------------------------------------------------------------ classification
ClassifiedBy = Literal["subject", "subject+regex", "regex", "pdf", "llm", "default"]


class ClassifiedFiling(Filing):
    category: str
    subtype: str | None = None
    classified_by: ClassifiedBy
    title: str                                  # cleaned one-liner (NSE text, or PDF 'Sub:' line)
    pdf_subject: str | None = None              # 'Sub:' line from page 1, when read
    amounts_cr: list[float] = Field(default_factory=list)
    amount_note: str | None = None              # e.g. order-size band used instead of a stated amount
    fx_mentions: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)
    primary_person: str | None = None          # person in the sentence that set the subtype


# --------------------------------------------------------------------- brief
Tier = Literal["needs_you", "glance", "set_aside"]


class ScorePart(BaseModel):
    part: Literal["category", "subtype", "uncertain", "amount", "numbers", "guidance", "reaction", "thesis"]
    points: float
    note: str


class Reaction(BaseModel):
    session: date
    ret: float                    # fraction
    abn_ret: float                # vs NIFTY, fraction
    z: float | None               # abnormal return / 60-session sigma
    vol_x: float | None           # volume / 20-session average


class ResultsRef(BaseModel):
    period_key: str | None
    xbrl_url: str | None
    consolidated: bool | None


class FilingRef(BaseModel):
    id: str
    nse_subject: str
    title: str
    category: str
    subtype: str | None
    classified_by: ClassifiedBy
    filed_at: datetime
    url: str | None


# ---------------------------------------------------------------- cards (phase 3)
class Metric(BaseModel):
    """One P&L line on a results card. Growth is None when there is no comparable base,
    and `note` says why (n/a for lenders, loss base, mixed sources, ...)."""
    key: str                      # revenue | ebitda | pbt | pat | nii | ppop
    label: str                    # "EBITDA (screener)"
    value: float | None           # INR crore
    prev_q: float | None = None
    prev_y: float | None = None
    qoq: float | None = None      # fraction
    yoy: float | None = None
    source: str | None = None     # "screener" | "NSE XBRL"
    note: str | None = None


class Margin(BaseModel):
    key: str                      # ebitda_margin | pat_margin | nii_margin
    label: str
    value: float | None           # fraction
    prev_q: float | None = None
    prev_y: float | None = None
    qoq_bps: float | None = None
    yoy_bps: float | None = None


class Check(BaseModel):
    """Arithmetic identity on the reported numbers, e.g. PBT - tax = PAT."""
    name: str
    expected: float
    actual: float
    tolerance: float
    ok: bool


class GuidedDelivered(BaseModel):
    """Phase 6: 'guided X last call -> delivered Y'. Graded in Python."""
    metric: str
    guided: str                   # as said on the call
    delivered: str
    grade: Literal["exceeded", "met", "missed", "revised", "pending", "not_comparable"]
    source: dict[str, str] = Field(default_factory=dict)   # label, doc_id, section (page), quote, note
    call_label: str | None = None       # "Q1 FY27 call"
    promise_id: str | None = None       # the promise in the ledger (pull side) this row came from
    progress_flag: Literal["on_track", "behind"] | None = None   # full-year target, year to date
    note: str | None = None             # revision history


class OneOff(BaseModel):
    """A one-off the filing itself names (exceptional line, or 'excluding ... ₹X crore')."""
    description: str
    amount_cr: float
    affects: Literal["profit", "revenue", "both", "unclear"] = "unclear"
    effect: Literal["gain", "charge", "unclear"] = "unclear"   # gain = lifts reported profit
    quote: str
    source: str                   # filing URL, or "NSE XBRL"
    verified: bool                # quote + amount found verbatim in the source


class AdjustedMetric(BaseModel):
    """A figure the company itself states excluding one-offs. Growth computed in Python."""
    key: str                      # pbt | pat | ebitda | revenue
    label: str
    value: float
    prev_y: float | None
    prev_y_source: Literal["company", "screener"]
    yoy: float | None
    quote: str
    source: str


class ResultsCard(BaseModel):
    symbol: str
    period_key: str               # FY27Q2
    period_label: str             # Q2 FY27
    basis: Literal["consolidated", "standalone"]
    layout: Literal["standard", "bank"]
    source: Literal["screener", "NSE XBRL"]
    source_note: str
    headline: str                 # built in code from the numbers
    metrics: list[Metric]
    margins: list[Margin]
    checks: list[Check]
    verified: bool
    status: str                   # "verified" | "unverified, open filing"
    filing_url: str | None = None
    xbrl_url: str | None = None
    guidance: list[GuidedDelivered] = Field(default_factory=list)
    guidance_note: str = "guided vs delivered from the last call: phase 6"
    one_offs: list[OneOff] = Field(default_factory=list)
    adjusted: list[AdjustedMetric] = Field(default_factory=list)
    reported_headline: str | None = None  # the as-reported sentence when a one-off changed the headline
    one_offs_by: str | None = None


class PriorRole(BaseModel):
    org: str
    role: str | None = None
    quote: str                    # sentence from the filing it came from


class PersonChange(BaseModel):
    name: str
    role: str | None
    change: Literal["appointed", "resigned", "retired", "ceased", "re-appointed", "redesignated", "other"]
    effective: str | None         # as written in the filing
    reason: str | None = None
    background: str | None = None # one line from the profile, verbatim-checked
    prior_roles: list[PriorRole] = Field(default_factory=list)
    dropped: list[str] = Field(default_factory=list)   # employers the model offered but the text lacks
    linkedin_search: str
    source: dict[str, str] = Field(default_factory=dict)


class MgmtCard(BaseModel):
    symbol: str
    headline: str
    people: list[PersonChange]
    short: str | None = None      # phone form: "Chairman: Noel Tata → Bahram Vakil"
    extracted_by: str             # "gemini:gemini-3.5-flash-lite" | "regex (LLM unavailable)"
    filing_urls: list[str]


class Event(BaseModel):
    id: str
    symbol: str
    company: str
    category: str
    subtype: str | None
    title: str
    occurred_at: datetime
    filings: list[FilingRef]
    people: list[str] = Field(default_factory=list)
    amount_cr: float | None = None
    reaction: Reaction | None = None
    reaction_pending: bool = False
    results_ref: ResultsRef | None = None
    score: float
    breakdown: list[ScorePart]
    tier: Tier
    summary: str | None = None            # one line for raw cover-letter titles (flash-lite)
    summary_by: str | None = None
    results_card: ResultsCard | None = None
    mgmt_card: MgmtCard | None = None


class LadderStats(BaseModel):
    filings: int
    by_step: dict[str, int]
    pdfs_read: int
    llm_calls: int                 # uncached calls this build
    llm_filings: int               # filings sent to a model
    never_touched_model: int
    never_touched_model_pct: float
    llm_status: str


class Move(BaseModel):
    """An unusual EOD session for a holding: |abnormal z| >= 2 or volume >= 2.5x."""
    symbol: str
    session: date
    close: float
    ret: float                    # fraction
    abn_ret: float                # vs NIFTY
    z: float | None
    vol_x: float | None
    event_id: str | None = None   # the filing event that owns this session, if any
    spark: list[float] = Field(default_factory=list)   # last 30 closes


class BriefSnapshot(BaseModel):
    generated_at: datetime
    window_from: date
    window_to: date
    prices_as_of: date | None
    symbols: list[str]
    events: list[Event]
    tiers: dict[str, int]
    ladder: LadderStats
    cards: dict[str, int] = Field(default_factory=dict)   # results / mgmt cards, summaries
    llm_usage: str | None = None                          # "6 calls, 4 cached, ... $0.00x est."
    moves: list[Move] = Field(default_factory=list)       # unusual sessions, last 5, newest first


# ============================================================== phase 6: promise store
Grade = Literal["exceeded", "met", "missed", "pending", "revised", "not_comparable"]


class Cite(BaseModel):
    """Where a claim comes from: a transcript page, a screener table, or a filing."""
    label: str                    # "Q1 FY27 call p.5", "screener FY26", "AR FY26 p.112"
    doc_id: str | None = None     # URL (PDFs get #page=N)
    page: int | None = None
    quote: str | None = None


class Promise(BaseModel):
    """One forward-looking statement from a concall, graded in Python (numeric) or by the
    strong model citing both calls (qualitative)."""
    id: str
    symbol: str
    call_quarter: str             # results quarter the call discussed, FY27Q1
    call_date: date
    call_label: str               # "Q1 FY27 call"
    speaker: str | None = None
    kind: Literal["numeric", "qualitative"]
    metric: str                   # as said, short: "Jewellery EBIT margin"
    metric_key: str               # revenue_growth, ebitda_margin, ebit_margin, ... other
    segment: str | None = None
    target_text: str | None = None   # "11-13%", "around 11%", "at least 20%"
    low: float | None = None
    high: float | None = None
    unit: str | None = None       # %, cr, count, bps, x
    qualifier: Literal["range", "at_least", "at_most", "around", "exact", "qualitative"] = "qualitative"
    horizon_text: str | None = None
    horizon: str | None = None    # FY27, FY27Q3, FY27H2, FY29 (multi-year end), None = unspecified
    horizon_kind: Literal["quarter", "half", "year", "multi_year", "unspecified"] = "unspecified"
    source: Cite
    grade: Grade = "pending"
    grade_note: str = ""
    actual: float | None = None
    actual_text: str | None = None
    actual_source: Cite | None = None
    progress: str | None = None   # "H1 FY27 +18% vs guided 11-13%"
    progress_flag: Literal["on_track", "behind"] | None = None
    revised_from: list[str] = Field(default_factory=list)   # earlier targets, oldest first: "10-12% (Q4 FY26 call)"
    revised_by: str | None = None  # id of the later promise that replaced this one
    revision_dir: Literal["up", "down", "same"] | None = None
    judged_by: str = "python"


class CompanyActual(BaseModel):
    """A number management stated as delivered (segment growth, store adds, cc growth),
    used to grade guidance screener can't see. Verbatim-checked like promises."""
    metric: str
    metric_key: str
    segment: str | None = None
    value: float
    unit: str
    period: str | None            # resolved: FY27Q2, FY27H1, FY26
    source: Cite


class Ledger(BaseModel):
    symbol: str
    built_at: datetime
    calls: list[Cite]             # the transcripts read, newest first
    promises: list[Promise]
    actuals: list[CompanyActual] = Field(default_factory=list)
    stats: dict[str, int] = Field(default_factory=dict)   # graded/met/missed/... + dropped (quote not found)
    notes: list[str] = Field(default_factory=list)
    llm: str | None = None


# ============================================================== phase 6: research dossier
class Vital(BaseModel):
    key: str                      # growth, margin, cash, roe, de, valuation
    label: str
    value: str                    # formatted at build time for the chip
    raw: float | None = None
    off: bool = False             # coloured only when something's off
    note: str                     # what it is, the threshold, and the source
    source: Cite


class Point(BaseModel):
    text: str
    cites: list[Cite]
    flagged: bool = False         # a number in the text not found in its cites


class Section(BaseModel):
    key: str
    title: str
    conclusion: str               # shown when the section is closed
    rows: list[dict[str, Any]] = Field(default_factory=list)
    series: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ResearchSnapshot(BaseModel):
    symbol: str
    company: str
    built_at: datetime
    price: float | None = None
    price_date: date | None = None
    day_change: float | None = None
    vitals: list[Vital]
    verdict: list[Point]
    sections: list[Section]
    ledger: Ledger
    bull: list[Point] = Field(default_factory=list)
    bear: list[Point] = Field(default_factory=list)
    questions: list[Point] = Field(default_factory=list)
    sources: list[Cite] = Field(default_factory=list)
    cost_line: str | None = None
    notes: list[str] = Field(default_factory=list)
