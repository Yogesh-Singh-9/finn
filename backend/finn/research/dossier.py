"""Research dossier for any NSE ticker: screener tables + latest annual report + last 4
concall transcripts. Nothing else (no news, no broker notes, no model knowledge).

Order of work: numbers (Python) -> ledger (flash-lite extracts, Python grades) ->
verdict strip (code) -> bull / bear / 3 questions (strong model, cites ids only; any
number in a point that isn't in its cited items is flagged, not shown as fact)."""
from __future__ import annotations

import io
import json
import logging
import re
from datetime import date, datetime
from typing import Any, Callable

from pydantic import BaseModel

from .. import config, llm
from ..http import FetchError, Fetcher
from ..models import Cite, Ledger, Point, ResearchSnapshot, Section
from . import business, financials, ledger as L, transcripts as T

log = logging.getLogger("finn.dossier")
PROMPT_VERSION = "dossier-v1"
PROMISE_STORE = config.PROCESSED_DIR / "promises"
RESEARCH_DIR = config.SNAPSHOT_DIR / "research"

Progress = Callable[[str], None]


def _fmt_pct(x: float | None, d: int = 0) -> str:
    return "n/a" if x is None else f"{x * 100:.{d}f}%".replace("-", "−")


# ---------------------------------------------------------------- annual report
_AR_KEYS = re.compile(r"risks? and concerns|opportunities and threats|outlook|threats|key risks|risk management|"
                      r"management discussion", re.I)


def annual_report_excerpts(f: Fetcher | None, fund: dict, max_pages: int = 3, scan: int = 160) -> tuple[list[dict], Cite | None, str | None]:
    """MD&A outlook / risk pages from the latest annual report: [{page, text}]."""
    ars = fund.get("annual_reports") or []
    if not ars or f is None:
        return [], None, "no annual report linked on screener" if not ars else "offline"
    ar = ars[0]
    cite = Cite(label=f"Annual report FY{str(ar['year'])[-2:]}", doc_id=ar["url"])
    try:
        data = f.get_bytes(ar["url"], cache="forever")
        from pypdf import PdfReader
        r = PdfReader(io.BytesIO(data))
        scored = []
        for i in range(min(scan, len(r.pages))):
            t = r.pages[i].extract_text() or ""
            hits = len(_AR_KEYS.findall(t))
            if hits and len(t) > 800:
                scored.append((hits, i, t))
        scored.sort(reverse=True)
        out = [{"page": i + 1, "text": re.sub(r"\s+", " ", t)[:2200]} for _, i, t in sorted(scored[:max_pages], key=lambda x: x[1])]
        return out, cite, None if out else "annual report has no readable MD&A risk/outlook pages"
    except (FetchError, Exception) as e:  # noqa: BLE001 - fail soft
        return [], cite, f"annual report unavailable ({type(e).__name__})"


# ---------------------------------------------------------------- verdict (code)
def verdict(facts: dict[str, Any], led: Ledger, vitals: list, url: str) -> list[Point]:
    scr = Cite(label="screener", doc_id=url)
    pts: list[tuple[int, Point]] = []
    graded = [p for p in led.promises if p.grade in ("exceeded", "met", "missed")]
    if graded or led.promises:
        good = sum(p.grade in ("exceeded", "met") for p in graded)
        miss = sum(p.grade == "missed" for p in graded)
        rev = [p for p in led.promises if p.grade == "revised"]
        nq = sum(p.kind == "qualitative" for p in graded)
        txt = (f"Delivered {good} of {len(graded)} promises it can be held to" + (f" ({nq} judged from a later call)" if nq else "")
               if graded else f"{len(led.promises)} promises on record, none gradeable yet")
        if miss:
            txt += f"; missed {miss}"
        if rev:
            txt += f"; {len(rev)} target{'s' if len(rev) > 1 else ''} revised {rev[0].grade_note.split()[1] if rev[0].grade_note else ''}".rstrip()
        pts.append((0 if miss or rev else 2, Point(text=txt, cites=[Cite(label="Promise ledger")])))
    watch = next((p for p in led.promises if p.kind == "numeric" and p.grade == "pending" and p.call_date == max(x.call_date for x in led.promises)), None)
    if watch:
        pts.append((3, Point(text=f"Watch next: {watch.metric}{f' ({watch.segment})' if watch.segment and watch.segment.lower() not in watch.metric.lower() else ''} "
                                  f"guided {watch.target_text}" + (f" for {watch.horizon}" if watch.horizon else ""),
                             cites=[watch.source])))
    r5, m, md = facts.get("rev_cagr_5y"), facts.get("ebitda_margin_latest"), facts.get("margin_vs_avg_bps")
    if r5 is not None:
        pts.append((4, Point(text=f"Revenue compounded {_fmt_pct(r5)} a year over 5 years, PAT {_fmt_pct(facts.get('pat_cagr_5y'))}", cites=[scr])))
    if m is not None and md is not None:
        pts.append((1 if md < -financials.MARGIN_DROP_BPS else 5,
                    Point(text=f"EBITDA margin {_fmt_pct(m, 1)} in {facts['latest_fy']}, {abs(md):.0f} bps {'above' if md >= 0 else 'below'} its 5-year average", cites=[scr])))
    c = facts.get("cfo_pat_5y")
    if c is not None:
        txt = f"Turned {c:.2f}× of 5-year profit into operating cash"
        if facts.get("negative_fcf_years"):
            txt += f"; free cash flow negative in {', '.join(facts['negative_fcf_years'])}"
        pts.append((1 if c < financials.CASH_MIN else 6, Point(text=txt, cites=[scr])))
    de = facts.get("de")
    if de is not None and de > financials.DE_MAX:
        pts.append((1, Point(text=f"Borrowings ₹{facts['borrowings']:,.0f} cr, D/E {de:.2f} (screener doesn't split working-capital lines)", cites=[scr])))
    elif facts.get("roe") is not None:
        pts.append((7, Point(text=f"ROE {_fmt_pct(facts['roe'])} in {facts['latest_fy']}" + (f", D/E {de:.2f}" if de is not None else ""), cites=[scr])))
    pts.sort(key=lambda x: x[0])
    return [p for _, p in pts[:5]]


# ---------------------------------------------------------------- divergences (code) -> questions
def divergences(facts: dict[str, Any], led: Ledger) -> list[tuple[str, list[Cite]]]:
    out: list[tuple[int, str, list[Cite]]] = []
    for p in led.promises:
        if p.grade == "missed":
            out.append((0, f"Guided {p.metric} {p.target_text} on the {p.call_label}; delivered {p.actual_text} ({p.grade_note})", [p.source] + ([p.actual_source] if p.actual_source else [])))
        elif p.grade == "revised":
            out.append((1, f"{p.metric}: {p.target_text} on the {p.call_label}, then {p.grade_note}", [p.source]))
        elif p.progress_flag == "behind":
            out.append((1, f"{p.metric} guided {p.target_text} for {p.horizon}; {p.progress}", [p.source]))
        elif p.kind == "qualitative" and p.grade == "pending" and p.judged_by != "python":
            out.append((3, f"Still in progress: {p.metric} ({p.call_label}): {p.grade_note}", [p.source]))
    md = facts.get("margin_vs_avg_bps")
    if md is not None and md < -financials.MARGIN_DROP_BPS:
        out.append((2, f"EBITDA margin {abs(md):.0f} bps below its 5-year average", [Cite(label="screener P&L")]))
    c = facts.get("cfo_pat_5y")
    if c is not None and c < financials.CASH_MIN:
        out.append((2, f"Only {c:.2f}× of 5-year profit became operating cash", [Cite(label="screener cash flow")]))
    if facts.get("ccc") is not None and facts.get("ccc_5y_ago") is not None and facts["ccc"] - facts["ccc_5y_ago"] > 20:
        out.append((2, f"Cash conversion cycle {facts['ccc']:.0f} days vs {facts['ccc_5y_ago']:.0f} five years ago", [Cite(label="screener ratios")]))
    de = facts.get("de")
    if de is not None and de > financials.DE_MAX:
        out.append((2, f"D/E {de:.2f}", [Cite(label="screener balance sheet")]))
    for p in led.promises:
        if p.kind == "numeric" and p.grade == "pending" and not p.progress_flag:
            out.append((4, f"Open target: {p.metric} {p.target_text} for {p.horizon or 'no stated horizon'} ({p.call_label})", [p.source]))
    out.sort(key=lambda x: x[0])
    return [(t, c) for _, t, c in out]


# ---------------------------------------------------------------- bull / bear / questions (strong model)
_SYS = (
    "You are an equity analyst writing for a long-term investor who owns this stock. Use ONLY the numbered items "
    "provided (F = computed facts, L = management's promise record, E = excerpts from the company's own concall "
    "transcripts and annual report). Answer only from the provided items; say 'not disclosed' otherwise. No outside "
    "knowledge, no news, no calculations: quote numbers exactly as they appear in the items you cite.\n"
    "Return bull (3 points), bear (3 points), and questions (exactly 3 questions to ask management on the next "
    "concall, built from the DIVERGENCES list, most important first). Each point: text (one sentence, plain, max 30 "
    "words) and cites (the item ids it rests on, e.g. [\"F2\",\"L1\"]). Return {\"bull\": [...], \"bear\": [...], "
    "\"questions\": [...]}."
)
_PT = {"type": "OBJECT", "properties": {"text": {"type": "STRING"}, "cites": {"type": "ARRAY", "items": {"type": "STRING"}}},
       "required": ["text", "cites"]}
_SCHEMA = {"type": "OBJECT", "properties": {"bull": {"type": "ARRAY", "items": _PT}, "bear": {"type": "ARRAY", "items": _PT},
                                            "questions": {"type": "ARRAY", "items": _PT}}, "required": ["bull", "bear", "questions"]}


class _P(BaseModel):
    text: str
    cites: list[str] = []


class _BB(BaseModel):
    bull: list[_P] = []
    bear: list[_P] = []
    questions: list[_P] = []


def _nums(s: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", s)}


def _items(facts: dict, sections: list[Section], led: Ledger, tr: list[T.Transcript], ar: list[dict], ar_cite: Cite | None,
           url: str) -> dict[str, tuple[str, Cite]]:
    items: dict[str, tuple[str, Cite]] = {}
    scr = Cite(label="screener", doc_id=url)
    for i, s in enumerate(sections, start=1):
        items[f"F{i}"] = (f"{s.title}: {s.conclusion}", Cite(label=f"screener · {s.title.lower()}", doc_id=url))
    n = len(items)
    for k, label in [("rev_cagr_3y", "Revenue CAGR 3 years"), ("ccc", "Cash conversion cycle (days)"), ("inventory_days", "Inventory days"),
                     ("roce", "ROCE")]:
        v = facts.get(k)
        if v is not None:
            n += 1
            items[f"F{n}"] = (f"{label}: {_fmt_pct(v) if k in ('rev_cagr_3y', 'roce') else f'{v:.0f}'}", scr)
    for i, p in enumerate([p for p in led.promises if p.grade != "not_comparable"][:14], start=1):
        txt = f"{p.call_label}: {p.metric}" + (f" ({p.segment})" if p.segment else "") + \
              (f" guided {p.target_text}" if p.target_text else f": \"{(p.source.quote or '')[:160]}\"") + \
              (f" for {p.horizon}" if p.horizon else "") + f" -> {p.grade}" + \
              (f", delivered {p.actual_text}" if p.actual_text and p.kind == "numeric" else "") + \
              (f"; {p.progress}" if p.progress else "") + (f" ({p.grade_note})" if p.grade_note else "")
        items[f"L{i}"] = (txt, p.source)
    e = 0
    if tr:
        latest = tr[0]
        for line in T.passages(latest, cap_chars=7000).split("\n"):
            m = re.match(r"\[p\.(\d+)\]\s*(.*)", line)
            if m:
                e += 1
                pg = int(m.group(1))
                items[f"E{e}"] = (f"{latest.call_label} p.{pg}: {m.group(2)[:600]}", Cite(label=f"{latest.call_label} p.{pg}", doc_id=f"{latest.url}#page={pg}", page=pg))
    for a in ar:
        e += 1
        items[f"E{e}"] = (f"{ar_cite.label if ar_cite else 'Annual report'} p.{a['page']}: {a['text'][:1400]}",
                          Cite(label=f"{ar_cite.label if ar_cite else 'AR'} p.{a['page']}", doc_id=f"{ar_cite.doc_id}#page={a['page']}" if ar_cite else None, page=a["page"]))
    return items


def bull_bear(facts: dict, sections: list[Section], led: Ledger, tr: list[T.Transcript], ar: list[dict], ar_cite: Cite | None,
              url: str, company: str, use_llm: bool = True) -> tuple[list[Point], list[Point], list[Point], str | None]:
    div = divergences(facts, led)
    fallback_q = [Point(text=f"{t}: what changed, and what should we expect next quarter?", cites=c) for t, c in div[:3]]
    if not use_llm or not llm.available("strong"):
        return [], [], fallback_q, None
    items = _items(facts, sections, led, tr, ar, ar_cite, url)
    user = f"Company: {company}\n\nITEMS\n" + "\n".join(f"{k}: {v[0]}" for k, v in items.items()) + \
           "\n\nDIVERGENCES (ranked)\n" + ("\n".join(f"- {t}" for t, _ in div[:6]) or "- none found")
    try:
        out, meta = llm.json_call_meta(tier="strong", purpose="dossier_bull_bear", prompt_version=PROMPT_VERSION,
                                       system=_SYS, user=user, schema=_SCHEMA, result_type=_BB)
    except llm.LLMUnavailable as e:
        log.warning("bull/bear unavailable: %s", e)
        return [], [], fallback_q, None
    by = f"{meta.provider}:{meta.model}"

    def conv(ps: list[_P]) -> list[Point]:
        res = []
        for p in ps:
            ids = [c.strip() for c in p.cites if c.strip() in items]
            if not ids:
                continue   # every claim has a source, or it isn't shown
            src = " ".join(items[i][0] for i in ids) + " " + " ".join(t for t, _ in div)
            bad = _nums(p.text) - _nums(src)
            res.append(Point(text=p.text, cites=[items[i][1].model_copy(update={"quote": items[i][0][:300]}) for i in ids], flagged=bool(bad)))
        return res

    qs = conv(out.questions)[:3] or fallback_q
    return conv(out.bull)[:3], conv(out.bear)[:3], qs, by


# ---------------------------------------------------------------- build
def _adjusted_from_brief(symbol: str) -> dict[str, dict]:
    path = config.SNAPSHOT_DIR / "brief.json"
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    try:
        b = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return out
    for e in b.get("events", []):
        c = e.get("results_card")
        if c and c.get("symbol") == symbol and c.get("adjusted"):
            out[c["period_key"]] = {a["key"]: a for a in c["adjusted"]}
    return out


def _price(symbol: str) -> dict | None:
    path = config.PROCESSED_DIR / "prices.json"
    if path.exists():
        try:
            for s in json.loads(path.read_text(encoding="utf-8")).get("series", []):
                if s["symbol"] == symbol and s.get("close"):
                    c = s["close"]
                    return {"close": c[-1], "date": s["dates"][-1], "chg": (c[-1] / c[-2] - 1) if len(c) > 1 and c[-2] else None}
        except (ValueError, KeyError):
            pass
    try:   # a never-seen ticker: one yfinance call, fail soft
        from ..sources.prices import fetch_prices
        series, _ = fetch_prices([symbol], period="5d")
        if series and series[0].close:
            s = series[0]
            return {"close": s.close[-1], "date": s.dates[-1].isoformat() if hasattr(s.dates[-1], "isoformat") else str(s.dates[-1]),
                    "chg": (s.close[-1] / s.close[-2] - 1) if len(s.close) > 1 else None}
    except Exception as e:  # noqa: BLE001
        log.info("price unavailable for %s: %s", symbol, e)
    return None


def build(symbol: str, f: Fetcher | None, fund: dict, use_llm: bool = True, progress: Progress = lambda s: None) -> ResearchSnapshot:
    llm.reset_usage()
    notes: list[str] = []
    url = fund.get("source_url") or f"https://www.screener.in/company/{symbol}/"
    price = _price(symbol)
    progress("Computing the numbers from screener's tables…")
    vitals, sections, facts = financials.build(fund, price)
    if price is None:
        notes.append("price unavailable; valuation chips show n/a")

    progress("Fetching the last 4 concall transcripts…")
    tr, tnotes = T.load(f, symbol, fund)
    notes += tnotes
    progress(f"Reading {len(tr)} transcript{'s' if len(tr) != 1 else ''} for guidance…")
    led = L.build(symbol, fund, tr, _adjusted_from_brief(symbol), use_llm=use_llm, notes=tnotes)
    PROMISE_STORE.mkdir(parents=True, exist_ok=True)
    (PROMISE_STORE / f"{symbol}.json").write_text(led.model_dump_json(), encoding="utf-8")

    progress("Reading the annual report's MD&A…")
    ar, ar_cite, ar_note = annual_report_excerpts(f, fund)
    if ar_note:
        notes.append(ar_note)

    progress("Reading the annual report's segment note for the business snapshot…")
    biz, _, bnotes = business.build(f, fund, use_llm)
    notes += bnotes

    progress("Weighing bull vs bear and drafting questions…")
    bull, bear, qs, _by = bull_bear(facts, sections, led, tr, ar, ar_cite, url, fund.get("company", symbol), use_llm)
    if not bull and use_llm:
        notes.append("bull/bear unavailable (strong models timed out or hit the live time budget); questions built from the ledger by rules")

    ledger_sec = Section(key="ledger", title="Promise ledger", conclusion=_ledger_conclusion(led))
    u = llm.USAGE
    cost = f"{u.calls} LLM calls, {u.cached} cached, {u.tokens_in + u.tokens_out:,} tokens, ${u.cost_usd:.4f} est."
    sources = [Cite(label="screener (P&L, balance sheet, cash flow, ratios)", doc_id=url)] + led.calls + ([ar_cite] if ar_cite and ar else [])
    snap = ResearchSnapshot(
        symbol=symbol, company=fund.get("company", symbol), built_at=datetime.now().replace(microsecond=0),
        price=price["close"] if price else None, price_date=date.fromisoformat(price["date"][:10]) if price else None,
        day_change=price.get("chg") if price else None, vitals=vitals, verdict=verdict(facts, led, vitals, url),
        sections=([biz] if biz else []) + [ledger_sec] + sections, ledger=led, bull=bull, bear=bear, questions=qs, sources=sources,
        cost_line=cost, notes=notes)
    return snap


def _ledger_conclusion(led: Ledger) -> str:
    s = led.stats
    graded = s.get("exceeded", 0) + s.get("met", 0) + s.get("missed", 0)
    if not led.promises:
        return "No forward-looking statements found in the transcripts read"
    bits = [f"{len(led.promises)} promises from {len(led.calls)} calls"]
    if graded:
        bits.append(f"{s.get('exceeded', 0) + s.get('met', 0)} of {graded} gradeable met or beaten")
    if s.get("missed"):
        bits.append(f"{s['missed']} missed")
    if s.get("revised"):
        bits.append(f"{s['revised']} revised")
    if s.get("pending"):
        bits.append(f"{s['pending']} pending")
    return ", ".join(bits)


def write(snap: ResearchSnapshot) -> None:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    data = snap.model_dump(mode="json")
    (RESEARCH_DIR / f"{snap.symbol}.json").write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
    # index of precomputed dossiers, so the Research tab can list them without a backend
    idx_path = RESEARCH_DIR / "index.json"
    try:
        idx = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else []
    except ValueError:
        idx = []
    idx = [x for x in idx if x.get("symbol") != snap.symbol]
    idx.append({"symbol": snap.symbol, "company": snap.company, "built_at": data["built_at"],
                "promises": len(snap.ledger.promises)})
    order = {s: i for i, s in enumerate(config.RESEARCH_DEMO_ORDER)}
    idx.sort(key=lambda x: (order.get(x["symbol"], len(order)), x["symbol"]))
    idx_path.write_text(json.dumps(idx, ensure_ascii=True), encoding="utf-8")
