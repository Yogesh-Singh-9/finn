"""Pydantic models for normalised source data. These are the contract between
ingestion, snapshot builders and (mirrored in TS) the frontend.

Units: money in INR crore (float); percentages as fractions (0.142).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

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
    part: Literal["category", "subtype", "uncertain", "amount", "reaction", "thesis"]
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


class LadderStats(BaseModel):
    filings: int
    by_step: dict[str, int]
    pdfs_read: int
    llm_calls: int                 # uncached calls this build
    llm_filings: int               # filings sent to a model
    never_touched_model: int
    never_touched_model_pct: float
    llm_status: str


class BriefSnapshot(BaseModel):
    generated_at: datetime
    window_from: date
    window_to: date
    prices_as_of: date | None
    symbols: list[str]
    events: list[Event]
    tiers: dict[str, int]
    ladder: LadderStats
