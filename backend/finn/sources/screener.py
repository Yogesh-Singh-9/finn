"""screener.in company page: standardised tables + document links.

The quarterly table is the single source for quarterly actuals (incl. the
"delivered" side of promises). XBRL is only used for a quarter screener lacks.
"""
from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from .. import config
from ..http import FetchError, Fetcher
from ..models import AnnualReportDoc, ConcallDoc, Fundamentals, SourceStatus, Table
from ..periods import fy_key, parse_month_label, quarter_key

TABLE_SECTIONS = {
    "quarters": "quarters",
    "profit-loss": "profit_loss",
    "balance-sheet": "balance_sheet",
    "cash-flow": "cash_flow",
    "ratios": "ratios",
}


def _num(cell: str) -> float | None:
    s = cell.replace(",", "").replace(" ", "").strip()
    if not s:
        return None
    pct = s.endswith("%")
    s = s.rstrip("%")
    try:
        v = float(s)
    except ValueError:
        return None
    return round(v / 100, 6) if pct else v


def _label(td: Tag) -> str:
    return re.sub(r"\s+", " ", td.get_text(" ", strip=True)).rstrip("+ ").strip()


def _parse_table(section: Tag | None, quarterly: bool) -> Table | None:
    table = section.select_one("table.data-table") if section else None
    if table is None:
        return None
    labels = [th.get_text(strip=True) for th in table.select("thead th")][1:]
    if not labels:
        return None
    cols: list[str] = []
    for lab in labels:
        ym = parse_month_label(lab)
        if ym is None:
            cols.append(lab or "?")
        else:
            cols.append((quarter_key(*ym) if quarterly else fy_key(*ym)) or lab)
    rows: dict[str, list[float | None]] = {}
    for tr in table.select("tbody tr"):
        tds = tr.select("td")
        if len(tds) < 2:
            continue
        name = _label(tds[0])
        if not name or name.lower() == "raw pdf":
            continue
        vals = [_num(td.get_text(strip=True)) for td in tds[1:]]
        vals = (vals + [None] * len(cols))[: len(cols)]
        if any(v is not None for v in vals):
            rows[name] = vals
    return Table(columns=cols, column_labels=labels, rows=rows) if rows else None


def _parse_documents(soup: BeautifulSoup) -> tuple[list[ConcallDoc], list[AnnualReportDoc]]:
    concalls: list[ConcallDoc] = []
    reports: list[AnnualReportDoc] = []
    for block in soup.select("section#documents div.documents"):
        h = block.select_one("h3")
        title = h.get_text(strip=True).lower() if h else ""
        if title.startswith("annual report"):
            for a in block.select("li a[href]"):
                m = re.search(r"(\d{4})", a.get_text())
                if m:
                    reports.append(AnnualReportDoc(year=int(m.group(1)), url=a["href"]))
        elif title.startswith("concall"):
            for li in block.select("li"):
                first = li.find("div")
                period = first.get_text(strip=True) if first else ""
                doc = ConcallDoc(period_label=period)
                for a in li.select("a[href]"):
                    kind = a.get_text(strip=True).lower()
                    # "AI Summary" is screener's own commentary (secondary) -> ignored.
                    if kind.startswith("transcript"):
                        doc.transcript_url = a["href"]
                    elif kind == "ppt":
                        doc.ppt_url = a["href"]
                    elif kind in ("rec", "recording"):
                        doc.recording_url = a["href"]
                if period and (doc.transcript_url or doc.ppt_url or doc.recording_url):
                    concalls.append(doc)
    return concalls, reports


def _has_quarters(soup: BeautifulSoup) -> bool:
    t = _parse_table(soup.select_one("section#quarters"), quarterly=True)
    return bool(t and len(t.rows) >= 3)


def fetch_fundamentals(f: Fetcher, symbol: str) -> tuple[Fundamentals | None, SourceStatus]:
    """Consolidated first; fall back to standalone when the company has no consolidated data."""
    soup = None
    basis = "consolidated"
    url = ""
    last_err = "no data"
    for basis, suffix in (("consolidated", "consolidated/"), ("standalone", "")):
        url = f"{config.SCREENER_BASE}/company/{symbol}/{suffix}"
        try:
            html = f.get_text(url, cache="daily", headers={"Accept": "text/html"})
        except FetchError as e:
            last_err = str(e)
            if e.status == 404:
                break  # unknown symbol: standalone will 404 too
            continue
        s = BeautifulSoup(html, "lxml")
        if _has_quarters(s):
            soup = s
            break
        last_err = f"{basis}: no quarterly table"
    if soup is None:
        return None, SourceStatus(source="screener", symbol=symbol, ok=False, error=last_err)

    tables = {attr: _parse_table(soup.select_one(f"section#{sid}"), quarterly=(sid == "quarters"))
              for sid, attr in TABLE_SECTIONS.items()}
    q = tables["quarters"]
    layout = "bank" if q and any(k.startswith("Financing Profit") for k in q.rows) else "standard"
    concalls, reports = _parse_documents(soup)
    h1 = soup.select_one("h1")
    fund = Fundamentals(
        symbol=symbol,
        company=h1.get_text(strip=True) if h1 else None,
        basis=basis,  # type: ignore[arg-type]
        layout=layout,  # type: ignore[arg-type]
        fetched_at=datetime.now().replace(microsecond=0),
        source_url=url,
        concalls=concalls,
        annual_reports=reports,
        **tables,
    )
    n_tx = sum(1 for c in concalls if c.transcript_url)
    return fund, SourceStatus(
        source="screener", symbol=symbol, ok=True,
        detail=(f"{basis}/{layout}, {len(q.columns) if q else 0} quarters to "
                f"{q.columns[-1] if q else '-'}, {n_tx} transcripts, {len(reports)} ARs"),
    )
