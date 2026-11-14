"""NSE: corporate announcements, results filings (+XBRL), event calendar.

Every public function fails soft: it returns (data, SourceStatus) and never raises.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import quote

from .. import config
from ..http import FetchError, Fetcher
from ..models import CalendarEvent, Filing, ResultsFilingRef, SourceStatus, XbrlResults
from ..periods import quarter_key_for_date

API = f"{config.NSE_BASE}/api"
IF_FINANCIALS = "Integrated Filing- Financials"


def _dmy(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def _parse_dt(s: str | None, fmts: tuple[str, ...]) -> datetime | None:
    if not s:
        return None
    for f in fmts:
        try:
            return datetime.strptime(s.strip(), f)
        except ValueError:
            continue
    return None


def _size_bytes(s: str | None) -> int | None:
    m = re.match(r"([\d.]+)\s*(B|KB|MB|GB|Bytes)", s or "", re.I)
    if not m:
        return None
    mult = {"b": 1, "bytes": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9}[m.group(2).lower()]
    return int(float(m.group(1)) * mult)


def warmup(f: Fetcher) -> None:
    """Best-effort homepage hit for cookies. It 403s locally and on runners; the
    API works without it, so failure is ignored."""
    try:
        f.get_bytes(f"{config.NSE_BASE}/", cache=None, headers={"Accept": "text/html"})
    except FetchError:
        pass


# ------------------------------------------------------------- announcements
def announcements(f: Fetcher, symbol: str, start: date, end: date) -> tuple[list[Filing], SourceStatus]:
    url = (f"{API}/corporate-announcements?index=equities&symbol={quote(symbol)}"
           f"&from_date={_dmy(start)}&to_date={_dmy(end)}")
    try:
        rows = f.get_json(url, cache="daily")
    except FetchError as e:
        return [], SourceStatus(source="nse.announcements", symbol=symbol, ok=False, error=str(e))
    if not isinstance(rows, list):
        return [], SourceStatus(source="nse.announcements", symbol=symbol, ok=False,
                                error=f"unexpected shape: {type(rows).__name__}")

    out: dict[str, Filing] = {}
    for r in rows:
        filed = _parse_dt(r.get("sort_date"), ("%Y-%m-%d %H:%M:%S",)) or \
            _parse_dt(r.get("an_dt"), ("%d-%b-%Y %H:%M:%S",))
        sid = str(r.get("seq_id") or "")
        if not filed or not sid:
            continue
        att = r.get("attchmntFile") or None
        out[sid] = Filing(
            id=sid,
            symbol=r.get("symbol") or symbol,
            company=r.get("sm_name") or symbol,
            nse_subject=(r.get("desc") or "").strip(),
            text=re.sub(r"\s+", " ", r.get("attchmntText") or "").strip(),
            filed_at=filed,
            attachment_url=att,  # used exactly as returned (host differs on runners)
            attachment_bytes=_size_bytes(r.get("fileSize") or r.get("attFileSize")),
            has_xbrl=bool(r.get("hasXbrl")),
            industry=r.get("smIndustry") or None,
        )
    filings = sorted(out.values(), key=lambda x: x.filed_at, reverse=True)
    dupes = len(rows) - len(filings)
    return filings, SourceStatus(source="nse.announcements", symbol=symbol, ok=True,
                                 detail=f"{len(filings)} filings" + (f", {dupes} dropped/dupes" if dupes else ""))


# ------------------------------------------------------------ results refs
def results_filings(f: Fetcher, symbol: str) -> tuple[list[ResultsFilingRef], SourceStatus]:
    """Quarterly results filings, newest first. Since Q3 FY25 these come from the
    Integrated Filing endpoint (the legacy corporates-financial-results stops at Dec 2024)."""
    url = f"{API}/integrated-filing-results?index=equities&symbol={quote(symbol)}&type={quote(IF_FINANCIALS)}"
    try:
        payload = f.get_json(url, cache="daily")
    except FetchError as e:
        return [], SourceStatus(source="nse.results", symbol=symbol, ok=False, error=str(e))
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    refs: list[ResultsFilingRef] = []
    for r in rows:
        pe = _parse_dt(r.get("qe_Date"), ("%d-%b-%Y",))
        if not pe:
            continue
        refs.append(ResultsFilingRef(
            symbol=symbol,
            period_end=pe.date(),
            period_key=quarter_key_for_date(pe.date()),
            consolidated=(r.get("consolidated") or "").lower().startswith("consol"),
            audited=(r.get("audited") or "").lower() == "audited",
            broadcast_at=_parse_dt(r.get("broadcast_Date"), ("%d-%b-%Y %H:%M:%S",)),
            xbrl_url=r.get("xbrl") or None,
            filing_type=r.get("type") or IF_FINANCIALS,
            seq_id=str(r.get("seq_Id") or ""),
        ))
    # Newest period first; for the same period prefer the latest broadcast (revisions).
    refs.sort(key=lambda x: (x.period_end, x.broadcast_at or datetime.min), reverse=True)
    return refs, SourceStatus(source="nse.results", symbol=symbol, ok=True, detail=f"{len(refs)} filings")


def latest_results_ref(refs: list[ResultsFilingRef]) -> ResultsFilingRef | None:
    """Latest quarter, consolidated preferred (standalone if the company has no subsidiaries)."""
    if not refs:
        return None
    latest_pe = refs[0].period_end
    same = [r for r in refs if r.period_end == latest_pe and r.xbrl_url]
    return next((r for r in same if r.consolidated), same[0] if same else None)


# --------------------------------------------------------------------- XBRL
_CTX_RE = re.compile(r'<xbrli:context id="([^"]+)">(.*?)</xbrli:context>', re.S)
_FACT_RE = re.compile(r'<[\w\-]+:(\w+)\s+contextRef="([^"]+)"[^>]*>([^<]*)<')
_TEXT_RE = re.compile(r'<[\w\-]+:(\w+)\s+contextRef="[^"]+"[^>]*>([^<]+)<')


def parse_xbrl(xml: str, symbol: str, url: str) -> XbrlResults:
    """Extract non-dimensional numeric facts for the quarter (OneD) and YTD (FourD).
    Values are in rupees in the file; we convert to crore. Prefix-agnostic:
    the integrated filing uses `in-capmkt:`, the legacy one used `in-bse-fin:`."""
    plain_ctx = {cid for cid, body in _CTX_RE.findall(xml) if "explicitMember" not in body}
    quarter: dict[str, float] = {}
    ytd: dict[str, float] = {}
    for tag, ctx, val in _FACT_RE.findall(xml):
        if ctx not in plain_ctx or ctx not in ("OneD", "FourD"):
            continue
        try:
            v = float(val) / 1e7
        except ValueError:
            continue
        (quarter if ctx == "OneD" else ytd).setdefault(tag, round(v, 4))

    text = {}
    for tag, val in _TEXT_RE.findall(xml):
        text.setdefault(tag, val.strip())
    start = _parse_dt(text.get("DateOfStartOfReportingPeriod"), ("%Y-%m-%d",))
    end = _parse_dt(text.get("DateOfEndOfReportingPeriod"), ("%Y-%m-%d",))
    nature = text.get("NatureOfReportStandaloneConsolidated", "").lower()
    # Per-share and ratio facts aren't crore; drop the obvious ones so nobody misuses them.
    for d in (quarter, ytd):
        for k in [k for k in d if "PerShare" in k or k.endswith(("Ratio", "Percentage"))]:
            d.pop(k)
    return XbrlResults(
        symbol=symbol,
        period_key=quarter_key_for_date(end.date()) if end else None,
        period_start=start.date() if start else None,
        period_end=end.date() if end else None,
        consolidated=("consol" in nature) if nature else None,
        source_url=url,
        quarter=quarter,
        ytd=ytd,
    )


def fetch_xbrl(f: Fetcher, ref: ResultsFilingRef) -> tuple[XbrlResults | None, SourceStatus]:
    if not ref.xbrl_url:
        return None, SourceStatus(source="nse.xbrl", symbol=ref.symbol, ok=False, error="no xbrl url")
    try:
        xml = f.get_text(ref.xbrl_url, cache="forever")
    except FetchError as e:
        return None, SourceStatus(source="nse.xbrl", symbol=ref.symbol, ok=False, error=str(e))
    res = parse_xbrl(xml, ref.symbol, ref.xbrl_url)
    ok = bool(res.quarter)
    return res, SourceStatus(source="nse.xbrl", symbol=ref.symbol, ok=ok,
                             detail=f"{res.period_key} {len(res.quarter)} facts",
                             error=None if ok else "no quarter facts parsed")


# ----------------------------------------------------------------- calendar
def event_calendar(f: Fetcher, symbols: set[str] | None = None) -> tuple[list[CalendarEvent], SourceStatus]:
    try:
        rows = f.get_json(f"{API}/event-calendar?index=equities", cache="daily")
    except FetchError as e:
        return [], SourceStatus(source="nse.calendar", ok=False, error=str(e))
    out = []
    for r in rows if isinstance(rows, list) else []:
        d = _parse_dt(r.get("date"), ("%d-%b-%Y",))
        sym = r.get("symbol") or ""
        if not d or (symbols and sym not in symbols):
            continue
        out.append(CalendarEvent(symbol=sym, company=r.get("company") or sym,
                                 purpose=r.get("purpose") or "", description=r.get("bm_desc") or "",
                                 date=d.date()))
    out.sort(key=lambda e: e.date)
    return out, SourceStatus(source="nse.calendar", ok=True, detail=f"{len(out)} events")
