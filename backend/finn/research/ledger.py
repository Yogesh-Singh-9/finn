"""Promise Ledger: what management guided on each call, and what they delivered.

1. flash-lite extracts forward-looking statements (and stated actuals) per transcript:
   metric, target as written, horizon as written, verbatim quote, page.
2. Python verifies every quote on its page (else dropped), parses the target (ranges,
   "at least", "around", "double digit"), resolves the horizon to a fiscal period, and
   grades numeric promises against screener (company level) or against a number the
   company itself stated on a later call (segments, constant currency, EBIT). One-offs:
   PAT is graded on the company's adjusted figure when the results card has one.
3. Revisions: same metric + segment + horizon with a different target on a later call.
4. Qualitative promises go to the strong chain, which must cite the later call; the
   evidence quote is verified the same way.
The model never computes a growth rate, a margin or a grade."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from .. import llm
from ..cards.results import _Q, prev_y
from ..models import Cite, CompanyActual, GuidedDelivered, Ledger, Promise
from .transcripts import Transcript, find_page, norm, passages

log = logging.getLogger("finn.ledger")
PROMPT_VERSION = "ledger-v1"
JUDGE_VERSION = "ledger-judge-v1"
MAX_QUAL_JUDGED = 8

METRIC_KEYS = [
    "revenue_growth", "revenue_growth_cc", "revenue", "ebitda_margin", "ebit_margin", "gross_margin",
    "pat_growth", "pat_margin", "pbt_growth", "capex", "debt", "tax_rate", "volume_growth", "store_count",
    "capacity", "order_book", "market_share", "attrition", "utilisation", "other",
]
SCREENER_KEYS = {"revenue_growth", "revenue", "ebitda_margin", "pat_growth", "pat_margin", "pbt_growth",
                 "capex", "debt", "tax_rate"}
LOWER_BETTER = {"debt", "attrition"}
NEUTRAL = {"capex", "tax_rate"}          # outside the range either way is a miss, not a beat

_SYSTEM = (
    "You read excerpts of an Indian listed company's earnings-call transcript. Each line starts with [p.N] (the PDF "
    "page) and the speaker. Answer only from the provided excerpts; copy words exactly as written; never use outside "
    "knowledge, never calculate.\n"
    "Return two lists.\n"
    "guidance: forward-looking statements by company management (not analysts, not the moderator) about a future "
    "period: targets, ranges, expectations, plans (\"we expect margins of 11-13%\", \"we will open 30 stores this year\", "
    "\"growth should be double digit in H2\"). SKIP backward-looking sentences (\"revenue grew 16%\") and refusals to "
    "guide. For each: speaker; metric (short, as said, e.g. 'Jewellery EBIT margin'); metric_key from the enum "
    "(operating margin / EBIT margin = ebit_margin; EBITDA margin = ebitda_margin; constant-currency growth = "
    "revenue_growth_cc; use 'other' if none fits); segment (business segment or null for the whole company); "
    "kind = numeric if the target has a number or a phrase like 'double digit', else qualitative; target_text = the "
    "target words exactly as written ('11-13%', 'around 11%', 'at least 20%', 'double digit', null if qualitative); "
    "low / high = the numbers as written in target_text (null if absent; 'at least 20%' -> low 20, high null); "
    "unit ('%', 'cr', 'count', 'bps', 'x', or other word); qualifier (range, at_least, at_most, around, exact, "
    "qualitative); horizon_text = the time words exactly as written ('this year', 'FY28', 'H2', 'next 2-3 years', "
    "null if none); quote = the full sentence copied exactly; page = N from the [p.N] tag.\n"
    "actuals: numbers management states as already delivered for a period, only for segments or metrics like "
    "constant-currency growth, EBIT/operating margin, store additions, volume growth: metric, metric_key, segment, "
    "value (as written), unit, period_text (as written: 'this quarter', 'H1', 'Q2', 'FY26'), quote, page.\n"
    "Return {\"guidance\": [...], \"actuals\": [...]}. Empty lists are fine."
)
_S = {"type": "STRING"}
_SN = {"type": "STRING", "nullable": True}
_NN = {"type": "NUMBER", "nullable": True}
_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "guidance": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "speaker": _SN, "metric": _S, "metric_key": {"type": "STRING", "enum": METRIC_KEYS}, "segment": _SN,
            "kind": {"type": "STRING", "enum": ["numeric", "qualitative"]}, "target_text": _SN, "low": _NN, "high": _NN,
            "unit": _SN, "qualifier": {"type": "STRING", "enum": ["range", "at_least", "at_most", "around", "exact", "qualitative"]},
            "horizon_text": _SN, "quote": _S, "page": {"type": "INTEGER"}},
            "required": ["metric", "metric_key", "kind", "qualifier", "quote", "page"]}},
        "actuals": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "metric": _S, "metric_key": {"type": "STRING", "enum": METRIC_KEYS}, "segment": _SN, "value": {"type": "NUMBER"},
            "unit": _S, "period_text": _SN, "quote": _S, "page": {"type": "INTEGER"}},
            "required": ["metric", "metric_key", "value", "unit", "quote", "page"]}},
    },
    "required": ["guidance", "actuals"],
}


class _G(BaseModel):
    speaker: str | None = None
    metric: str
    metric_key: str = "other"
    segment: str | None = None
    kind: Literal["numeric", "qualitative"] = "qualitative"
    target_text: str | None = None
    low: float | None = None
    high: float | None = None
    unit: str | None = None
    qualifier: Literal["range", "at_least", "at_most", "around", "exact", "qualitative"] = "qualitative"
    horizon_text: str | None = None
    quote: str
    page: int = 0


class _A(BaseModel):
    metric: str
    metric_key: str = "other"
    segment: str | None = None
    value: float
    unit: str = "%"
    period_text: str | None = None
    quote: str
    page: int = 0


class _Out(BaseModel):
    guidance: list[_G] = []
    actuals: list[_A] = []


# ---------------------------------------------------------------- periods
def _fy_of(d: date) -> int:
    return (d.year + 1 if d.month >= 4 else d.year) % 100


def _q_of(d: date) -> int:
    return {4: 1, 5: 1, 6: 1, 7: 2, 8: 2, 9: 2, 10: 3, 11: 3, 12: 3, 1: 4, 2: 4, 3: 4}[d.month]


_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4}
_WORDN = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "couple of": 2, "few": 3, "2": 2, "3": 3, "4": 4, "5": 5}


def resolve_horizon(text: str | None, call_date: date) -> tuple[str | None, str]:
    """Horizon words -> fiscal period, relative to the call date. Table lookups only."""
    if not text:
        return None, "unspecified"
    t = text.lower().replace("’", "'")
    fy, q = _fy_of(call_date), _q_of(call_date)
    m = (re.search(r"\b(?:fy|fiscal|financial year)\s*'?\s*(20)?(\d{2})\b", t) or re.search(r"\b20(\d{2})\s*[-–/]\s*(20)?(\d{2})\b", t)
         or re.search(r"\b(?:by|till|until|through|in|to)\s+(20)(\d{2})\b", t) or re.fullmatch(r"\s*(20)(\d{2})\s*", t))
    explicit_fy = None
    if m:
        explicit_fy = int(m.groups()[-1])
    mq = re.search(r"\bq([1-4])\b|\b(first|second|third|fourth|1st|2nd|3rd|4th) quarter\b", t)
    if mq:
        qq = int(mq.group(1)) if mq.group(1) else _ORD[mq.group(2)]
        f = explicit_fy or (fy if qq >= q else fy + 1)
        return f"FY{f:02d}Q{qq}", "quarter"
    mh = re.search(r"\bh([12])\b|\b(first|second) half\b", t)
    if mh:
        hh = int(mh.group(1)) if mh.group(1) else _ORD[mh.group(2)]
        f = explicit_fy or (fy if (hh == 2 or q <= 2) else fy + 1)
        return f"FY{f:02d}H{hh}", "half"
    if re.search(r"next (quarter|coming quarter)|coming quarter|following quarter", t):
        nq, nf = (q + 1, fy) if q < 4 else (1, fy + 1)
        return f"FY{nf:02d}Q{nq}", "quarter"
    mm = re.search(r"next (one|two|three|four|five|couple of|few|\d)\s*(?:to\s*\w+\s*)?(?:-\s*\d\s*)?years?", t)
    if mm:
        return f"FY{fy + _WORDN.get(mm.group(1), 3):02d}", "multi_year"
    if re.search(r"medium[- ]term|mid[- ]term", t):
        return f"FY{fy + 3:02d}", "multi_year"
    if re.search(r"long(er)?[- ]term|over time|years to come", t):
        return f"FY{fy + 5:02d}", "multi_year"
    if explicit_fy:
        return f"FY{explicit_fy:02d}", ("year" if explicit_fy <= fy + 1 else "multi_year")
    if re.search(r"next (year|fiscal|financial year)", t):
        return f"FY{fy + 1:02d}", "year"
    if re.search(r"this (year|fiscal|financial year)|current (year|fiscal)|full[- ]year|for the year|the year|rest of the (?:financial |fiscal )?year|annual", t):
        return f"FY{fy:02d}", "year"
    if re.search(r"this quarter|current quarter", t):
        return f"FY{fy:02d}Q{q}", "quarter"
    return None, "unspecified"


def resolve_period(text: str | None, call_quarter: str) -> str | None:
    """Period of a stated actual, relative to the quarter the call discusses."""
    fy, q = int(call_quarter[2:4]), int(call_quarter[5])
    if not text:
        return call_quarter
    t = text.lower()
    m = re.search(r"\b(?:fy|fiscal)\s*'?\s*(20)?(\d{2})\b", t)
    mq = re.search(r"\bq([1-4])\b|\b(first|second|third|fourth) quarter\b", t)
    if mq:
        qq = int(mq.group(1)) if mq.group(1) else _ORD[mq.group(2)]
        return f"FY{int(m.group(2)) if m else fy:02d}Q{qq}"
    if re.search(r"\bh1\b|first half|half year|half-year|six months", t):
        return f"FY{int(m.group(2)) if m else fy:02d}H1"
    if re.search(r"\bh2\b|second half", t):
        return f"FY{int(m.group(2)) if m else fy:02d}H2"
    if m:
        return f"FY{int(m.group(2)):02d}"
    if re.search(r"full year|for the year|whole year|last year", t) and q == 4:
        return f"FY{fy:02d}"
    if re.search(r"quarter|this time|q-o-q|yoy|year-on-year", t):
        return call_quarter
    return call_quarter


# ---------------------------------------------------------------- targets
_PHRASES = [  # phrase -> (low, high, qualifier); a lookup, not a calculation
    (r"high[- ]single[- ]digit", 7, 9, "range"), (r"mid[- ]single[- ]digit", 4, 6, "range"),
    (r"low[- ]single[- ]digit", 1, 3, "range"), (r"low[- ]double[- ]digit", 10, 12, "range"),
    (r"mid[- ]teens?", 14, 16, "range"), (r"high[- ]teens?", 17, 19, "range"), (r"low[- ]teens?", 11, 13, "range"),
    (r"double[- ]digit", 10, None, "at_least"), (r"\bteens\b", 11, 19, "range"),
]


def unit_class(unit: str | None, text: str | None = None) -> str:
    t = f"{unit or ''} {text or ''}".lower()
    if "%" in t or "percent" in t:
        return "%"
    if "bps" in t or "basis point" in t:
        return "bps"
    if "$" in t or "usd" in t or "dollar" in t:
        return "usd"
    if re.search(r"\d\s*x\b|\btimes\b|^\s*x\s*$", t):
        return "x"
    if "day" in t:
        return "days"
    if re.search(r"crore|\bcr\b|billion|\bbn\b|million|\bmn\b|lakh|\binr\b|\brs\b|₹", t):
        return "cr"
    return "count"


def to_cr_factor(unit: str | None, text: str | None = None) -> float:
    """Scale of an INR amount relative to crore (a unit table, not a computed result)."""
    t = f"{unit or ''} {text or ''}".lower()
    if "lakh crore" in t:
        return 1e5
    if "billion" in t or re.search(r"\bbn\b", t):
        return 100.0
    if "million" in t or re.search(r"\bmn\b", t):
        return 0.1
    return 1.0


NATURAL = {"revenue_growth": "%", "revenue_growth_cc": "%", "ebitda_margin": "%", "ebit_margin": "%", "gross_margin": "%",
           "pat_growth": "%", "pat_margin": "%", "pbt_growth": "%", "tax_rate": "%", "volume_growth": "%", "attrition": "%",
           "utilisation": "%", "market_share": "%", "revenue": "cr", "capex": "cr", "debt": "cr", "order_book": "cr"}


def _nums(s: str | None) -> list[float]:
    return [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", s or "")]


def parse_target(g: _G) -> tuple[float | None, float | None, str]:
    """(low, high, qualifier) from the target words; numbers must be in the quote."""
    tt = (g.target_text or "").lower()
    for pat, lo, hi, qual in _PHRASES:
        # "double digit" counts even when the model called it qualitative (no target_text)
        if re.search(pat, g.quote.lower()) and (re.search(pat, tt) or (not tt and "growth" in g.metric_key)):
            return lo, hi, qual
    if re.search(r"\d\s*bps", tt) and "%" in tt:
        return None, None, "qualitative"   # "75 bps to 1%": mixed units, don't guess
    qn = set(_nums(g.quote))
    ns = [n for n in _nums(tt) if n in qn]
    if not ns:
        return None, None, "qualitative"
    if re.search(r"at least|minimum|upwards of|more than|over|above|north of|\+$", tt):
        return ns[0], None, "at_least"
    if re.search(r"up to|at most|below|under|less than|maximum|not more than|within", tt):
        return None, ns[0], "at_most"
    if len(ns) >= 2 and re.search(r"\d\s*(%|percent|x|times|bps|days|crores?|cr|billion|bn|million|mn)?\s*(-|–|to|and|or)\s*(inr|rs\.?|₹|\$)?\s*\d", tt):
        return min(ns[:2]), max(ns[:2]), "range"
    if re.search(r"around|about|approximately|~|close to|roughly|centre of gravity|center of gravity|plus.?minus", tt + " " + g.quote.lower()):
        return ns[0], ns[0], "around"
    return ns[0], ns[0], "exact"


# ---------------------------------------------------------------- screener actuals
class _Fin:
    def __init__(self, fund: dict, adjusted: dict[str, dict]):
        self.q = _Q(fund)
        pl = fund.get("profit_loss") or {}
        self.pl_cols = [c for c in pl.get("columns", []) if c.startswith("FY")]
        self.pl = pl
        self.cf = fund.get("cash_flow") or {}
        self.bs = fund.get("balance_sheet") or {}
        self.adjusted = adjusted       # period_key -> {"pat": value, "pat_prev": value, "quote": ...}
        self.url = fund.get("source_url")

    def _annual(self, table: dict, row: str, fy: str) -> float | None:
        cols, r = table.get("columns", []), (table.get("rows") or {}).get(row)
        if not r or fy not in cols:
            return None
        return r[cols.index(fy)]

    def _quarters(self, period: str) -> list[str] | None:
        fy = int(period[2:4])
        if "H" in period:
            qs = [f"FY{fy:02d}Q1", f"FY{fy:02d}Q2"] if period.endswith("H1") else [f"FY{fy:02d}Q3", f"FY{fy:02d}Q4"]
        elif "Q" in period:
            qs = [period]
        else:
            return None
        return qs if all(self.q.has(k) for k in qs) else None

    def _sum(self, row: str, qs: list[str]) -> float | None:
        vals = [self.q.get(row, k) for k in qs]
        return sum(vals) if all(v is not None for v in vals) else None

    def value(self, key: str, period: str) -> tuple[float, str, str] | None:
        """(actual, text, label) for a company-level metric over a fiscal period, or None."""
        if "Q" in period or "H" in period:
            qs = self._quarters(period)
            if not qs:
                return None
            cur = lambda row: self._sum(row, qs)  # noqa: E731
            base = lambda row: self._sum(row, [prev_y(k) for k in qs]) if all(self.q.has(prev_y(k)) for k in qs) else None  # noqa: E731
            label = f"screener {period}"
        else:
            if period not in self.pl_cols:
                return None
            prevfy = f"FY{int(period[2:4]) - 1:02d}"
            cur = lambda row: self._annual(self.pl, row, period)  # noqa: E731
            base = lambda row: self._annual(self.pl, row, prevfy)  # noqa: E731
            label = f"screener FY{period[2:4]} annual"

        def growth(row: str) -> float | None:
            c, b = cur(row), base(row)
            return (c / b - 1) * 100 if c is not None and b and b > 0 else None

        if key == "revenue_growth":
            g = growth("Sales")
            return (round(g, 1), f"{g:+.1f}% YoY", label) if g is not None else None
        if key == "revenue":
            v = cur("Sales")
            return (v, f"₹{v:,.0f} cr", label) if v is not None else None
        if key == "ebitda_margin":
            s, op = cur("Sales"), cur("Operating Profit")
            return (round(op / s * 100, 1), f"{op / s * 100:.1f}% (EBITDA, screener)", label) if s and op is not None else None
        if key in ("pat_growth", "pbt_growth"):
            row = "Net Profit" if key == "pat_growth" else "Profit before tax"
            adj = self.adjusted.get(period, {}).get("pat" if key == "pat_growth" else "pbt")
            if adj and adj.get("yoy") is not None:
                g = adj["yoy"] * 100
                return round(g, 1), f"{g:+.1f}% YoY (company's adjusted, excl. one-off)", f"company adjusted {period}"
            g = growth(row)
            return (round(g, 1), f"{g:+.1f}% YoY", label) if g is not None else None
        if key == "pat_margin":
            s, p = cur("Sales"), cur("Net Profit")
            return (round(p / s * 100, 1), f"{p / s * 100:.1f}%", label) if s and p is not None else None
        if key == "tax_rate":
            pbt, pat = cur("Profit before tax"), cur("Net Profit")
            return (round((1 - pat / pbt) * 100, 1), f"{(1 - pat / pbt) * 100:.1f}%", label) if pbt and pat is not None and pbt > 0 else None
        if key == "capex" and period in self.pl_cols:
            cfo = self._annual(self.cf, "Cash from Operating Activity", period)
            fcf = self._annual(self.cf, "Free Cash Flow", period)
            return (round(cfo - fcf), f"₹{cfo - fcf:,.0f} cr (CFO − FCF)", f"screener FY{period[2:4]} cash flow") if cfo is not None and fcf is not None else None
        if key == "debt" and period in self.pl_cols:
            v = self._annual(self.bs, "Borrowings", period)
            return (v, f"₹{v:,.0f} cr borrowings", f"screener FY{period[2:4]} balance sheet") if v is not None else None
        return None


# ---------------------------------------------------------------- grading
def _seg(s: str | None) -> str:
    s = (s or "").lower()
    s = re.sub(r"\b(business|division|segment|portfolio|vertical|the|our|overall|company|consolidated|standalone)\b", " ", s)
    return re.sub(r"[^a-z]+", " ", s).strip()


def _same_seg(a: str | None, b: str | None) -> bool:
    sa, sb = _seg(a), _seg(b)
    if not sa or not sb:
        return sa == sb
    ta, tb = set(sa.split()), set(sb.split())
    return bool(ta & tb)


def _grade(p: Promise, actual: float) -> tuple[str, str]:
    lo, hi, k = p.low, p.high, p.metric_key
    u = "%" if (p.unit or "").startswith("%") else f" {p.unit or ''}".rstrip()
    fmt = lambda v: f"{v:g}{u}"  # noqa: E731
    if p.qualifier in ("around", "exact") and lo is not None:
        tol = max(0.5, abs(lo) * 0.05) if u == "%" else abs(lo) * 0.05
        if abs(actual - lo) <= tol:
            return "met", f"{fmt(actual)} within ±{tol:g} of {fmt(lo)}"
        above = actual > lo
    elif p.qualifier == "at_least" and lo is not None:
        if actual >= lo:
            beat = actual >= lo + (max(1.0, abs(lo) * 0.1) if u == "%" else abs(lo) * 0.1)
            return ("exceeded" if beat and k not in NEUTRAL else "met"), f"{fmt(actual)} vs at least {fmt(lo)}"
        return "missed", f"{fmt(actual)} below the floor of {fmt(lo)}"
    elif p.qualifier == "at_most" and hi is not None:
        return ("met", f"{fmt(actual)} within the cap of {fmt(hi)}") if actual <= hi else ("missed", f"{fmt(actual)} above the cap of {fmt(hi)}")
    elif lo is not None and hi is not None:
        if lo <= actual <= hi:
            return "met", f"{fmt(actual)} inside {fmt(lo)}–{fmt(hi)}"
        above = actual > hi
    else:
        return "not_comparable", "target not parseable"
    if k in NEUTRAL:
        return "missed", f"{fmt(actual)} {'above' if above else 'below'} the guided {p.target_text}"
    good = above != (k in LOWER_BETTER)
    return ("exceeded" if good else "missed"), f"{fmt(actual)} {'above' if above else 'below'} the guided {p.target_text}"


def _actual_for(p: Promise, period: str, fin: _Fin, actuals: list[CompanyActual]) -> tuple[float, str, Cite] | None:
    company_level = not _seg(p.segment)
    if company_level and p.metric_key in SCREENER_KEYS:
        v = fin.value(p.metric_key, period)
        if v:
            return v[0], v[1], Cite(label=v[2], doc_id=fin.url)
    if p.metric_key == "other":
        return None   # too loose to match anything reliably
    pu = unit_class(p.unit, p.target_text)
    for a in actuals:   # company-stated, newest call first
        if a.metric_key == p.metric_key and a.period == period and _same_seg(a.segment, p.segment) \
                and unit_class(a.unit, a.source.quote if pu == "cr" else None) == pu:
            v = a.value * (to_cr_factor(a.unit, a.source.quote) if pu == "cr" else 1)
            return v, _fmt_val(v, pu) + " (company, on the call)", a.source
    return None


def _fmt_val(v: float, u: str) -> str:
    if u == "%":
        return f"{v:g}%"
    if u == "cr":
        return f"₹{v:,.0f} cr"
    if u in ("bps", "days"):
        return f"{v:g} {u}"
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:g}"


def grade_all(promises: list[Promise], fund: dict, actuals: list[CompanyActual], adjusted: dict[str, dict],
              latest_quarter: str | None) -> None:
    fin = _Fin(fund, adjusted)
    for p in promises:
        if p.kind != "numeric" or p.grade == "revised":
            continue
        if p.low is None and p.high is None:
            p.grade, p.grade_note = "not_comparable", "no number to grade against"
            continue
        pu = unit_class(p.unit, p.target_text)
        if pu == "x":
            p.grade, p.grade_note = "not_comparable", "relative target (a multiple of industry or market growth); FINN has no industry figure"
            continue
        nat = NATURAL.get(p.metric_key)
        if nat and pu != nat:
            p.grade, p.grade_note = "not_comparable", f"target in {pu}, {p.metric_key.replace('_', ' ')} is measured in {nat}"
            continue
        if p.horizon_kind == "multi_year":
            _track(p, fin, actuals)
            p.grade, p.grade_note = "pending", f"multi-year target ({p.horizon}); not due yet"
            continue
        if p.horizon_kind == "unspecified" and pu not in ("%", "bps", "days"):
            # a flow like "capex ₹1,200-1,600 cr a year": only a full year can be graded
            fys = [c for c in fin.pl_cols if c > f"FY{p.call_quarter[2:4]}"]
            if fys:
                p.horizon, p.horizon_kind = fys[0], "year"
            else:
                p.grade, p.grade_note = "pending", "annual figure; no full year reported since this call"
                continue
        if p.horizon_kind == "unspecified":
            # an ongoing target ("11% is the centre of gravity"): grade the latest quarter after the call
            after = [k for k in fin.q.cols if k > p.call_quarter]
            if not after:
                p.grade, p.grade_note = "pending", "no quarter reported since this call"
                continue
            hits = [(k, _actual_for(p, k, fin, actuals)) for k in after]
            hits = [(k, h) for k, h in hits if h]
            if not hits:
                p.grade, p.grade_note = "not_comparable", _nc_note(p)
                continue
            k, (v, txt, cite) = hits[-1]
            p.grade, note = _grade(p, v)  # type: ignore[assignment]
            p.actual, p.actual_text, p.actual_source = v, f"{txt} in {k[4:]} {k[:4]}", cite
            p.grade_note = f"no horizon stated; latest quarter {k}: {note}"
            continue
        period = p.horizon or ""
        got = _actual_for(p, period, fin, actuals)
        if got:
            v, txt, cite = got
            p.grade, p.grade_note = _grade(p, v)  # type: ignore[assignment]
            p.actual, p.actual_text, p.actual_source = v, txt, cite
            continue
        # not reported yet (or never comparable)
        if _reported(period, fin):
            p.grade, p.grade_note = "not_comparable", _nc_note(p)
            continue
        p.grade, p.grade_note = "pending", f"{period} not reported yet"
        if p.horizon_kind in ("year", "half") and latest_quarter:
            fy = int(period[2:4])
            done = [k for k in fin.q.cols if k.startswith(f"FY{fy:02d}Q")]
            ytd = None
            if len(done) >= 2 and done[:2] == [f"FY{fy:02d}Q1", f"FY{fy:02d}Q2"]:
                ytd = f"FY{fy:02d}H1"
            elif len(done) == 1:
                ytd = done[0]
            if ytd:
                g = _actual_for(p, ytd, fin, actuals)
                if g:
                    v = g[0]
                    gr, _ = _grade(p, v)
                    p.progress = f"{ytd[4:]} FY{fy}: {g[1]} vs guided {p.target_text}"
                    p.progress_flag = "behind" if gr == "missed" else "on_track"
                    p.actual_source = g[2]


def _track(p: Promise, fin: _Fin, actuals: list[CompanyActual]) -> None:
    """Multi-year target: where the latest reported quarter stands, flagged with the same
    rule the final grade will use."""
    if unit_class(p.unit, p.target_text) not in ("%", "bps", "days"):
        return   # a multi-year total (capex over 5 years) can't be read off one quarter
    for k in reversed(fin.q.cols[-4:]):
        g = _actual_for(p, k, fin, actuals)
        if g:
            gr, _ = _grade(p, g[0])
            p.progress = f"latest {k[4:]} {k[:4]}: {g[1]} vs target {p.target_text}"
            p.progress_flag = "behind" if gr == "missed" else "on_track"
            p.actual_source = g[2]
            return


def _reported(period: str, fin: _Fin) -> bool:
    if not period:
        return False
    if "Q" in period or "H" in period:
        return fin._quarters(period) is not None
    return period in fin.pl_cols


def _nc_note(p: Promise) -> str:
    if _seg(p.segment):
        return f"segment figure ({p.segment}); screener has no segment data and no later call states it"
    if p.metric_key not in SCREENER_KEYS:
        return f"{p.metric_key.replace('_', ' ')} is not in screener's tables and no later call states it"
    return "screener has no matching figure"


def link_revisions(promises: list[Promise]) -> None:
    """Same metric + segment + horizon on a later call with a different target: the older
    one is 'revised'; the newer carries the history."""
    groups: dict[tuple, list[Promise]] = {}
    for p in promises:
        if p.kind == "numeric" and (p.low is not None or p.high is not None):
            name = re.sub(r"[^a-z]+", " ", p.metric.lower()).strip() if p.metric_key == "other" else ""
            groups.setdefault((p.metric_key, _seg(p.segment), p.horizon or "ongoing", unit_class(p.unit, p.target_text), name), []).append(p)
    for g in groups.values():
        g.sort(key=lambda p: (p.call_date, p.id))
        for older, newer in zip(g, g[1:]):
            if older.call_date == newer.call_date:
                continue
            mid = lambda p: ((p.low if p.low is not None else p.high) + (p.high if p.high is not None else p.low)) / 2  # noqa: E731
            d = mid(newer) - mid(older)
            older.revised_by = newer.id
            newer.revised_from = older.revised_from + [f"{older.target_text} ({older.call_label})"]
            newer.revision_dir = "same" if abs(d) < 1e-9 else ("up" if d > 0 else "down")
            if newer.revision_dir != "same":
                older.grade = "revised"
                older.grade_note = f"revised {newer.revision_dir} to {newer.target_text} on the {newer.call_label}"
            else:
                older.grade_note = f"reiterated on the {newer.call_label}"


# ---------------------------------------------------------------- extraction
def _cite(t: Transcript, page: int, quote: str | None = None) -> Cite:
    return Cite(label=f"{t.call_label} p.{page}", doc_id=f"{t.url}#page={page}", page=page, quote=quote)


def extract(t: Transcript, use_llm: bool = True) -> tuple[list[Promise], list[CompanyActual], dict[str, int], str | None]:
    stats = {"extracted": 0, "dropped_quote": 0, "dropped_number": 0, "page_fixed": 0, "backward": 0}
    text = passages(t, cap_chars=20000)
    if not text or not use_llm or not llm.available("cheap"):
        return [], [], stats, None
    try:
        out, meta = llm.json_call_meta(tier="cheap", purpose="ledger_extract", prompt_version=PROMPT_VERSION,
                                       system=_SYSTEM, user=f"Company: {t.symbol}. Call: {t.call_label}, held {t.call_date}.\n\n{text}",
                                       schema=_SCHEMA, result_type=_Out)
    except llm.LLMUnavailable as e:
        log.warning("ledger extraction unavailable for %s %s: %s", t.symbol, t.label, e)
        return [], [], stats, None
    by = f"{meta.provider}:{meta.model}" + (" (cached)" if meta.cached else "")
    cd = date.fromisoformat(t.call_date)
    promises: list[Promise] = []
    seen: set[tuple] = set()
    for i, g in enumerate(out.guidance):
        stats["extracted"] += 1
        page = find_page(t, g.quote, g.page)
        if page is None:
            stats["dropped_quote"] += 1
            continue
        stats["page_fixed"] += int(page != g.page)
        if re.match(r"^\s*(we|it|revenue|sales|margin)\b.{0,60}\b(grew|was|were|declined|delivered|came in)\b", g.quote, re.I) \
                and not re.search(r"\b(will|expect|should|would|guid|target|aim|plan)\w*", g.quote, re.I):
            stats["backward"] += 1
            continue
        kind, lo, hi, qual = g.kind, None, None, "qualitative"
        lo, hi, qual = parse_target(g)
        if lo is not None or hi is not None:
            if kind == "qualitative":
                g.target_text = next((m.group(0) for pat, *_ in _PHRASES if (m := re.search(pat, g.quote, re.I))), g.target_text)
            if any(re.search(pat, (g.target_text or "").lower()) for pat, *_ in _PHRASES):
                g.unit = "%"   # "double digit growth" is a percentage
            kind = "numeric"
        elif kind == "numeric":
            stats["dropped_number"] += 1
            kind = "qualitative"
        if kind == "numeric" and unit_class(g.unit, g.target_text) == "cr":
            fac = to_cr_factor(g.unit, g.target_text)
            lo, hi = (lo * fac if lo is not None else None), (hi * fac if hi is not None else None)
        horizon, hk = resolve_horizon(g.horizon_text, cd)
        if g.horizon_text and norm(g.horizon_text) not in norm(t.pages[page - 1] + " " + (t.pages[page] if page < len(t.pages) else "")):
            horizon, hk = resolve_horizon(None, cd)   # horizon words not in the quote: don't trust them
        key = (g.metric_key, _seg(g.segment), horizon, lo, hi, kind, norm(g.quote)[:60] if kind == "qualitative" else "")
        if key in seen:
            continue
        seen.add(key)
        promises.append(Promise(
            id=f"{t.symbol}-{t.quarter}-{i}", symbol=t.symbol, call_quarter=t.quarter, call_date=cd, call_label=t.call_label,
            speaker=g.speaker, kind=kind, metric=g.metric, metric_key=g.metric_key if g.metric_key in METRIC_KEYS else "other",
            segment=g.segment or None, target_text=g.target_text if kind == "numeric" else None, low=lo, high=hi,
            unit=(g.unit or ("%" if "%" in (g.target_text or "") else None)), qualifier=qual if kind == "numeric" else "qualitative",
            horizon_text=g.horizon_text, horizon=horizon, horizon_kind=hk, source=_cite(t, page, g.quote),
            judged_by="python" if kind == "numeric" else "pending"))
    actuals: list[CompanyActual] = []
    for a in out.actuals:
        page = find_page(t, a.quote, a.page)
        if page is None or a.value not in _nums(a.quote):
            continue
        actuals.append(CompanyActual(metric=a.metric, metric_key=a.metric_key, segment=a.segment or None, value=a.value,
                                     unit=a.unit, period=resolve_period(a.period_text, t.quarter), source=_cite(t, page, a.quote)))
    return promises, actuals, stats, by


# ---------------------------------------------------------------- qualitative judge
_JSYS = (
    "You check whether company management delivered on qualitative promises made on an earlier earnings call, using "
    "excerpts from LATER calls by the same company. Answer only from the provided excerpts; if they don't settle it, "
    "grade 'unclear' and say 'not disclosed'. No outside knowledge. For each promise id return: grade (met = later "
    "call says it happened; missed = later call says it did not happen, was delayed or dropped; pending = later call "
    "says it is still in progress; unclear = not disclosed), reason (one short sentence, citing both calls by name), "
    "evidence_quote (exact sentence copied from a later-call excerpt, or null), evidence_call (its call name). "
    "Return {\"items\": [...]}."
)
_JSCHEMA = {"type": "OBJECT", "properties": {"items": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "id": _S, "grade": {"type": "STRING", "enum": ["met", "missed", "pending", "unclear"]}, "reason": _S,
    "evidence_quote": _SN, "evidence_call": _SN}, "required": ["id", "grade", "reason"]}}}, "required": ["items"]}


class _J(BaseModel):
    id: str
    grade: Literal["met", "missed", "pending", "unclear"]
    reason: str
    evidence_quote: str | None = None
    evidence_call: str | None = None


_STOP = set("the a an and or of to in for on with we our will be is are this that it as at by from have has would should "
            "next year quarter going forward also more very".split())


def _evidence(p: Promise, later: list[Transcript], per_call: int = 2) -> str:
    words = {w for w in re.findall(r"[a-z]{4,}", (p.metric + " " + (p.segment or "") + " " + (p.source.quote or "")).lower()) if w not in _STOP}
    out = []
    for t in later:
        scored = sorted(((len(words & set(re.findall(r"[a-z]{4,}", pg.lower()))), i) for i, pg in enumerate(t.pages)), reverse=True)
        for s, i in scored[:per_call]:
            if s >= 3:
                body = re.sub(r"\s+", " ", t.pages[i])[:1800]
                out.append(f"[{t.call_label} p.{i + 1}] {body}")
    return "\n".join(out)


_TOWARDS = re.compile(r"\b(towards?|approach\w*|inch\w*|gravitat\w*|mov(e|ing) (up )?to|get(ting)? to|path to|journey to|trajectory)\b", re.I)


def _reached(promise: str, evidence: str) -> bool | None:
    """Did the later call's number reach the level the promise was moving towards?
    True / False when both sides carry a comparable % level, None when it can't tell."""
    target = None
    for pat, lo, _hi, _q in _PHRASES:
        if re.search(pat, promise, re.I):
            target = lo
            break
    if target is None:
        m = re.search(r"(?:towards?|to|approach\w*)\s+(\d+(?:\.\d+)?)\s*%", promise, re.I)
        target = float(m.group(1)) if m else None
    got = re.findall(r"(\d+(?:\.\d+)?)\s*%", evidence)
    if target is None or not got:
        return None
    vals = [float(x) for x in got]
    if all(v >= target for v in vals):
        return True
    if all(v < target for v in vals):
        return False
    return None   # mixed numbers in the evidence: can't tell which is the level


def judge_qualitative(promises: list[Promise], transcripts: list[Transcript], use_llm: bool = True) -> str | None:
    by_label = {t.call_label: t for t in transcripts}
    todo = []
    for p in promises:
        if p.kind != "qualitative":
            continue
        later = [t for t in transcripts if t.call_date > p.call_date.isoformat()]
        if not later:
            p.grade, p.grade_note, p.judged_by = "pending", "made on the latest call; too early to judge", "python"
            continue
        todo.append((p, later))
    todo = todo[:MAX_QUAL_JUDGED]
    for p in promises:
        if p.kind == "qualitative" and p.judged_by == "pending" and all(p is not x for x, _ in todo):
            p.grade, p.grade_note, p.judged_by = "not_comparable", "not judged (over the per-dossier cap)", "python"
    if not todo or not use_llm or not llm.available("strong"):
        for p, _ in todo:
            p.grade, p.grade_note, p.judged_by = "not_comparable", "qualitative; model unavailable", "python"
        return None
    blocks = []
    for p, later in todo:
        blocks.append(f"PROMISE {p.id} ({p.call_label}, p.{p.source.page}): \"{p.source.quote}\"\nLATER-CALL EXCERPTS:\n{_evidence(p, later)}")
    try:
        items, meta = llm.json_call_meta(tier="strong", purpose="ledger_judge", prompt_version=JUDGE_VERSION, system=_JSYS,
                                         user="\n\n".join(blocks), schema=_JSCHEMA, result_type=list[_J], unwrap="items")
    except llm.LLMUnavailable as e:
        log.warning("qualitative judge unavailable: %s", e)
        for p, _ in todo:
            p.grade, p.grade_note, p.judged_by = "not_comparable", "qualitative; model unavailable", "python"
        return None
    by = f"{meta.provider}:{meta.model}" + (" (cached)" if meta.cached else "")
    got = {j.id: j for j in items}
    for p, later in todo:
        j = got.get(p.id)
        p.judged_by = by
        if not j or j.grade == "unclear":
            p.grade, p.grade_note = "not_comparable", (j.reason if j else "no verdict returned") if j else "no verdict returned"
            continue
        t = by_label.get(j.evidence_call or "") or next((x for x in later if j.evidence_quote and find_page(x, j.evidence_quote, None)), None)
        page = find_page(t, j.evidence_quote, None) if (t and j.evidence_quote) else None
        if j.grade in ("met", "missed") and page is None:
            p.grade, p.grade_note = "not_comparable", "model's evidence quote not found in the later call; not shown as fact"
            continue
        p.grade = j.grade  # type: ignore[assignment]
        p.grade_note = j.reason
        if j.grade == "met" and _TOWARDS.search(p.source.quote or ""):
            # "inching towards / approaching X" is met only once X is actually reached
            reached = _reached(p.source.quote or "", j.evidence_quote or "")
            if reached is not True:
                p.grade, p.progress_flag = "pending", "on_track"
                p.grade_note = ("on track, not yet met: the promise was to move towards a level, and the later call "
                                "shows progress short of it" if reached is False else
                                "on track at best: 'towards' promises count as met only when the level is reached, "
                                "and the later call doesn't show that") + f". {j.reason}"
        if page and t:
            p.actual_text = "per later call"
            p.actual_source = _cite(t, page, j.evidence_quote)
    return by


# ---------------------------------------------------------------- build
def build(symbol: str, fund: dict, transcripts: list[Transcript], adjusted: dict[str, dict],
          use_llm: bool = True, notes: list[str] | None = None) -> Ledger:
    promises: list[Promise] = []
    actuals: list[CompanyActual] = []
    stats: dict[str, int] = {}
    models: set[str] = set()
    # the calls are independent: extract in parallel (a live build waits on the slowest, not the sum)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(lambda t: extract(t, use_llm), transcripts))
    for ps, acts, st, by in results:  # newest first, so actuals from the latest call win
        promises += ps
        actuals += acts
        for k, v in st.items():
            stats[k] = stats.get(k, 0) + v
        if by:
            models.add(by.replace(" (cached)", ""))
    # same call, same metric + target: keep the copy whose horizon resolved
    resolved = {(p.call_quarter, p.metric_key, _seg(p.segment), p.low, p.high) for p in promises if p.kind == "numeric" and p.horizon}
    promises = [p for p in promises if p.kind != "numeric" or p.horizon
                or (p.call_quarter, p.metric_key, _seg(p.segment), p.low, p.high) not in resolved]
    link_revisions(promises)
    q = _Q(fund)
    grade_all(promises, fund, actuals, adjusted, q.cols[-1] if q.cols else None)
    judge = judge_qualitative(promises, transcripts, use_llm)
    if judge:
        models.add(judge.replace(" (cached)", ""))
    for g in ("exceeded", "met", "missed", "pending", "revised", "not_comparable"):
        stats[g] = sum(p.grade == g for p in promises)
    promises.sort(key=lambda p: (p.call_date, p.kind == "numeric"), reverse=True)
    return Ledger(symbol=symbol, built_at=datetime.now().replace(microsecond=0),
                  calls=[Cite(label=f"{t.call_label} ({t.call_date})", doc_id=t.url) for t in transcripts],
                  promises=promises, actuals=actuals, stats=stats, notes=notes or [],
                  llm=", ".join(sorted(models)) or None)


# ---------------------------------------------------------------- push side: the results card row
def card_rows(led: Ledger, fund: dict, period: str, results_date: date, adjusted: dict[str, dict],
              limit: int = 3) -> list[GuidedDelivered]:
    """'Guided X last call -> delivered Y' for the quarter just reported. Uses the latest
    guidance made BEFORE the results for each metric; grades it against this quarter
    (ongoing targets, quarter targets), or shows year-to-date progress (full-year targets)."""
    fin = _Fin(fund, adjusted)
    latest: dict[tuple, Promise] = {}
    for p in sorted(led.promises, key=lambda x: x.call_date):
        if p.kind != "numeric" or p.call_date >= results_date or (p.low is None and p.high is None):
            continue
        pu = unit_class(p.unit, p.target_text)
        if pu == "x" or (NATURAL.get(p.metric_key) and NATURAL[p.metric_key] != pu):
            continue
        name = re.sub(r"[^a-z]+", " ", p.metric.lower()).strip() if p.metric_key == "other" else ""
        latest[(p.metric_key, _seg(p.segment), p.horizon or "ongoing", pu, name)] = p
    fy, q = period[:4], int(period[5])
    ytd = f"{fy}H1" if q == 2 else period
    rows: list[tuple[int, GuidedDelivered]] = []
    for p in latest.values():
        pu = unit_class(p.unit, p.target_text)
        cur = p.model_copy()
        grade, delivered, src, rank, flag = None, None, None, 9, None
        if p.horizon == period or (p.horizon_kind == "unspecified" and pu in ("%", "bps", "days")):
            got = _actual_for(cur, period, fin, led.actuals)
            if got:
                grade, _note = _grade(cur, got[0])
                delivered, src = f"{got[1]} in {period[4:]}", got[2]
                rank = {"missed": 0, "exceeded": 1, "met": 2}.get(grade, 5)
        elif p.horizon in (fy, f"{fy}H1" if q <= 2 else f"{fy}H2") and p.horizon != period:
            got = _actual_for(cur, ytd, fin, led.actuals) if p.horizon == fy or q <= 2 else None
            if got:
                g2, _ = _grade(cur, got[0])
                grade, flag = "pending", ("behind" if g2 == "missed" else "on_track")
                delivered, src = f"{got[1]} so far ({ytd[4:]})", got[2]
                rank = 3 if flag == "behind" else 4
        elif p.horizon_kind == "multi_year" and pu in ("%", "bps", "days"):
            got = _actual_for(cur, period, fin, led.actuals)
            if got:
                g2, _ = _grade(cur, got[0])
                grade, flag = "pending", ("behind" if g2 == "missed" else "on_track")
                delivered, src = f"{got[1]} in {period[4:]}", got[2]
                rank = 6
        if grade is None:
            continue
        seg = f" ({p.segment})" if p.segment and p.segment.lower() not in p.metric.lower() else ""
        if p.metric_key == "revenue_growth_cc" and "cc" not in p.metric.lower() and "constant" not in p.metric.lower():
            seg += " (cc)"
        delivered = (delivered or "").replace(" (company, on the call)", "")
        hz = f" for {p.horizon}" if p.horizon and p.horizon != period else (" (ongoing)" if not p.horizon else "")
        rows.append((rank, GuidedDelivered(
            metric=f"{p.metric[:1].upper()}{p.metric[1:]}{seg}", guided=f"{p.target_text}{hz}", delivered=delivered or "",
            grade=grade, call_label=p.call_label, promise_id=p.id, progress_flag=flag,
            note=(f"revised: {' → '.join(p.revised_from)} → {p.target_text}" if p.revised_from and p.revision_dir != "same" else None),
            source={"label": p.call_label.replace(" call", ""), "doc_id": p.source.doc_id or "", "quote": p.source.quote or "",
                    "section": f"p.{p.source.page}", "note": f"Delivered: {src.label}" if src else ""})))
    rows.sort(key=lambda r: r[0])
    out, seen = [], set()
    for _, r in rows:   # one row per metric: the most decisive one
        k = r.metric.lower()
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out[:limit]
