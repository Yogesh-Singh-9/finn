"""Business snapshot: what the company does, its segments and revenue mix, from the latest
annual report (primary source only).

Rules pick the pages: an overview page from the front of the report ("who we are",
"at a glance", ...) and the segment note ("segment information" with a table of numbers).
flash-lite only extracts what is written: a one-line description with its quote, and each
segment's revenue as printed. Python then
  - checks every quote and every segment number on its page (else dropped),
  - reads the unit from the page itself ("₹ in crore" / "in lakhs" / "in million"), not
    from the model, and converts to crore,
  - computes the mix (share of the segments' total) and checks the total against screener's
    revenue for that year, saying when they differ (inter-segment, standalone basis)."""
from __future__ import annotations

import io
import logging
import re
from typing import Any

from pydantic import BaseModel

from .. import llm
from ..http import FetchError, Fetcher
from ..models import Cite, Section

log = logging.getLogger("finn.business")
PROMPT_VERSION = "business-v1"
MAX_SCAN = 450

_OVERVIEW = re.compile(r"who we are|about us|company overview|corporate overview|at a glance|our business|business overview|"
                       r"we are (india|one|a |the )|our presence|what we do|business model", re.I)
_SEGMENT = re.compile(r"segment (information|reporting|revenue|results)|operating segments?|reportable segments?", re.I)
# pypdf often renders ₹ as a backtick (a font-mapping quirk): "` in crore"
_SEGHEAD = re.compile(r"business segments|segment revenues?|segment information|revenue by (business )?segment|"
                      r"(reportable|operating) segments?\s*[:\n]", re.I)
_UNIT = re.compile(r"(?:₹|`|rs\.?|inr|rupees)\s*(?:in\s+)?(crores?|lakhs?|lacs?|millions?|billions?|thousands?)\b|"
                   r"\bin\s+(?:₹|`|rs\.?|inr)?\s*(crores?|lakhs?|lacs?|millions?|billions?)\b|"
                   r"\(\s*(?:₹|`|H|rs\.?|inr)?\s*(?:in\s+)?(crores?|lakhs?|lacs?|millions?|billions?|thousands?)\s*\)", re.I)   # "(H million)": ₹ as H
_TO_CR = {"crore": 1.0, "lakh": 0.01, "lac": 0.01, "million": 0.1, "billion": 100.0, "thousand": 0.0001}

_SYS = (
    "You read pages of an Indian listed company's annual report (each page starts with [p.N]). Answer only from these "
    "pages; copy quotes exactly; no outside knowledge; no calculations.\n"
    "1. what: what the company does, in max 30 plain words, and what_quote: one sentence copied exactly that supports "
    "it, and what_page.\n"
    "2. segments: the company's reportable business segments from the segment information note, with each segment's "
    "revenue for the LATEST year in that table, exactly as printed (digits and commas as on the page, no unit). Use "
    "revenue from external customers if shown, else segment revenue. Each: name, revenue, page, row (the table row text "
    "copied exactly, including the number). Skip totals, eliminations and unallocated rows. Empty if no segment table.\n"
    "3. year: the latest year of the segment table as written (e.g. 'year ended March 31, 2026'). "
    "4. basis: consolidated or standalone, if the page says.\n"
    "Return {\"what\", \"what_quote\", \"what_page\", \"segments\": [...], \"year\", \"basis\"}."
)
_S = {"type": "STRING"}
_SCHEMA = {"type": "OBJECT", "properties": {
    "what": _S, "what_quote": _S, "what_page": {"type": "INTEGER"}, "year": _S, "basis": _S,
    "segments": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "name": _S, "revenue": _S, "page": {"type": "INTEGER"}, "row": _S}, "required": ["name", "revenue", "page"]}}},
    "required": ["what", "segments"]}


class _Seg(BaseModel):
    name: str
    revenue: str
    page: int = 0
    row: str = ""


class _Out(BaseModel):
    what: str = ""
    what_quote: str = ""
    what_page: int = 0
    segments: list[_Seg] = []
    year: str = ""
    basis: str = ""


def _norm(s: str) -> str:
    s = (s or "").lower().replace("’", "'").replace("–", "-").replace("—", "-")
    s = re.sub(r"[^a-z0-9%.\-']+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _digits(s: str) -> str:
    """'12,345.60' -> '12345.6' style key, so Indian and western comma styles both match."""
    s = re.sub(r"[,\s]", "", s or "")
    m = re.search(r"\d+(?:\.\d+)?", s)
    if not m:
        return ""
    v = m.group(0)
    return v.rstrip("0").rstrip(".") if "." in v else v


def _page_numbers(text: str) -> set[str]:
    return {_digits(m.group(0)) for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", text)}


def read_pages(f: Fetcher | None, url: str) -> tuple[list[str], str | None]:
    if f is None:
        return [], "offline"
    try:
        from pypdf import PdfReader
        r = PdfReader(io.BytesIO(f.get_bytes(url, cache="forever")))
        return [(p.extract_text() or "") for p in r.pages[:MAX_SCAN]], None
    except (FetchError, Exception) as e:  # noqa: BLE001 - fail soft
        return [], f"annual report unreadable ({type(e).__name__})"


def _pick(pages: list[str]) -> tuple[list[int], list[int]]:
    ov = [(len(_OVERVIEW.findall(t)), i) for i, t in enumerate(pages[:40]) if _OVERVIEW.search(t) and len(t) > 400]
    ov.sort(key=lambda x: (-x[0], x[1]))
    seg = []
    for i, t in enumerate(pages):
        hits = len(_SEGMENT.findall(t))
        nums = len(re.findall(r"\d[\d,]{3,}(?:\.\d+)?", t))
        if hits and nums >= 12:
            # a numbers-heavy page under a segment-table heading beats a page that only talks about segments
            seg.append((min(hits, 2) * 2 + min(nums, 100) / 10 + (5 if _SEGHEAD.search(t) else 0)
                        + (2 if re.search(r"consolidated", t, re.I) else 0), i))
    seg.sort(key=lambda x: -x[0])
    return [i for _, i in ov[:2]], sorted(i for _, i in seg[:3])


def _unit(text: str) -> str | None:
    m = _UNIT.search(text)
    if not m:
        return None
    w = next(g for g in m.groups() if g).lower().rstrip("s")
    return "lakh" if w == "lac" else w


def build(f: Fetcher | None, fund: dict, use_llm: bool = True) -> tuple[Section | None, Cite | None, list[str]]:
    notes: list[str] = []
    ars = fund.get("annual_reports") or []
    if not ars:
        return None, None, ["business snapshot: no annual report linked on screener"]
    ar = ars[0]
    label = f"Annual report FY{str(ar['year'])[-2:]}"
    cite = Cite(label=label, doc_id=ar["url"])
    pages, err = read_pages(f, ar["url"])
    if err or not pages:
        return None, cite, [f"business snapshot: {err or 'no text layer'}"]
    ov, seg = _pick(pages)
    if not ov and not seg:
        return None, cite, ["business snapshot: no overview or segment-note page found in the annual report"]
    if not use_llm or not llm.available("cheap"):
        return None, cite, ["business snapshot: model unavailable"]
    chosen = sorted(set(ov + seg))
    squash = [re.sub(r"[ \t]+", " ", pages[i])[:5000] for i in chosen]   # no backslash inside f-string braces (3.11)
    user = f"Company: {fund.get('company', '')}\n\n" + "\n\n".join(f"[p.{i + 1}]\n{t}" for i, t in zip(chosen, squash))
    try:
        out, meta = llm.json_call_meta(tier="cheap", purpose="business_snapshot", prompt_version=PROMPT_VERSION,
                                       system=_SYS, user=user, schema=_SCHEMA, result_type=_Out)
    except llm.LLMUnavailable as e:
        return None, cite, [f"business snapshot: model unavailable ({str(e)[:60]})"]
    by = f"{meta.provider}:{meta.model}"

    def on_page(quote: str, hint: int) -> int | None:
        q = _norm(quote)
        if len(q) < 15:
            return None
        order = ([hint - 1] if 1 <= hint <= len(pages) else []) + chosen + list(range(min(40, len(pages))))
        for i in order:
            if 0 <= i < len(pages) and q in _norm(pages[i]):
                return i + 1
        return None

    rows: list[dict[str, Any]] = []
    what, what_cite = None, None
    pg = on_page(out.what_quote, out.what_page)
    if out.what and pg:
        what = out.what.strip().rstrip(".")
        what_cite = Cite(label=f"{label} p.{pg}", doc_id=f"{ar['url']}#page={pg}", page=pg, quote=out.what_quote)
    elif out.what:
        notes.append("description dropped: its quote isn't on the cited page")

    segs = []
    for s in out.segments[:8]:
        key = _digits(s.revenue)
        page_i = s.page - 1 if 1 <= s.page <= len(pages) else None
        cands = ([page_i] if page_i is not None else []) + [i for i in seg if i != page_i]
        hit = next((i for i in cands if key and key in _page_numbers(pages[i])
                    and _norm(s.name).split(" ")[0] in _norm(pages[i])), None)
        if hit is None:
            notes.append(f"segment '{s.name}' dropped: {s.revenue} not found on the page")
            continue
        unit = _unit(pages[hit]) or next((_unit(pages[j]) for j in range(max(0, hit - 3), hit)[::-1] if _unit(pages[j])), None)
        if unit is None:
            notes.append(f"segment '{s.name}' dropped: no unit stated on its page")
            continue
        segs.append({"segment": s.name.strip(), "printed": s.revenue.strip(), "unit": unit, "value_cr": float(key) * _TO_CR[unit],
                     "page": hit + 1})
    if segs:
        total = sum(x["value_cr"] for x in segs)
        for x in segs:
            x["share"] = x["value_cr"] / total if total else None
        segs.sort(key=lambda x: -x["value_cr"])
        rows = [{**x, "source": Cite(label=f"{label} p.{x['page']}", doc_id=f"{ar['url']}#page={x['page']}", page=x["page"],
                                     quote=f"{x['segment']}: {x['printed']} ({x['unit']})").model_dump()} for x in segs]
        # cross-check the total with screener's revenue for that year
        fy = None
        ym = re.findall(r"20\d\d", out.year or "")
        if ym:
            fy = f"FY{int(ym[-1]) % 100:02d}" if not re.search(r"dec", out.year, re.I) else None
        sales = None
        pl = fund.get("profit_loss") or {}
        if fy and fy in pl.get("columns", []):
            v = (pl.get("rows", {}).get("Sales") or [None] * len(pl["columns"]))[pl["columns"].index(fy)]
            sales = float(v) if isinstance(v, (int, float)) else None
        if sales:
            r = total / sales
            notes.append(f"Segments total ₹{total:,.0f} cr vs screener revenue ₹{sales:,.0f} cr for {fy}"
                         + ("." if 0.9 <= r <= 1.12 else " (they differ: inter-segment sales, other income or a standalone note).")
                         + (f" Basis: {out.basis}." if out.basis else ""))
        elif out.year:
            notes.append(f"Segment year: {out.year}.")
    elif out.segments:
        notes.append("no segment survived the page check")
    else:
        notes.append("the annual report shows no segment table (likely a single-segment company)")

    if not what and not rows:
        return None, cite, notes
    geo = rows and all(re.search(r"europe|america|india|asia|africa|rest of (the )?world|domestic|international|overseas|"
                                 r"export|usa|\bus\b|\buk\b|middle east|apac|emea|others?", r["segment"], re.I) for r in rows)
    if rows and len(rows) > 1:
        top = rows[0]
        mix = f"{top['segment']} is {top['share'] * 100:.0f}% of segment revenue" + (" (segments are geographies)" if geo else "")
    elif rows:
        mix = f"one reportable segment: {rows[0]['segment']}"
    else:
        mix = "segment mix not disclosed in a readable table"
    conclusion = f"{what}. {mix[:1].upper()}{mix[1:]}" if what else mix[:1].upper() + mix[1:]
    sec = Section(key="business", title="Business snapshot", conclusion=conclusion, rows=rows,
                  series=[what_cite.model_dump()] if what_cite else [],
                  notes=notes + [f"Extracted by {by}; every number checked on its page, mix computed in code."])
    return sec, cite, []
