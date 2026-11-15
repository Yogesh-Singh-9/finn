"""Concall transcripts: fetch (BSE PDFs linked from screener), split into pages and
speaker turns, and pick the passages worth a model's time.

Rules run first: only sentences with a forward-looking cue (guidance) or a metric +
number in the past tense (company-stated actuals) reach flash-lite, each tagged with its
PDF page so the model can cite it and Python can verify the quote on that page."""
from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .. import config
from ..http import FetchError, Fetcher
from ..periods import parse_month_label

log = logging.getLogger("finn.transcripts")
STORE = config.PROCESSED_DIR / "transcripts"
N_CALLS = 4


@dataclass
class Transcript:
    symbol: str
    label: str                    # screener's "Nov 2026"
    url: str
    call_date: str                # ISO; from the transcript cover, else the 15th of the label month
    quarter: str                  # results quarter discussed, e.g. FY27Q2
    pages: list[str] = field(default_factory=list)

    @property
    def call_label(self) -> str:
        return f"{self.quarter[-2:]} {self.quarter[:4]} call"   # "Q2 FY27 call"


# ---------------------------------------------------------------- normalise / verify
def norm(s: str | None) -> str:
    """Whitespace, quotes, dashes and line-break hyphenation folded, so a quote copied
    from the PDF text matches regardless of how pypdf wrapped it."""
    s = (s or "").lower()
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    s = s.replace("₹", "rs ").replace("rs.", "rs ")
    s = re.sub(r"-\s*\n\s*", "-", s)
    s = re.sub(r"[^a-z0-9%.+\-]+", " ", s)
    s = re.sub(r"\s*-\s*", "-", s)
    return re.sub(r"\s+", " ", s).strip()


def find_page(t: Transcript, quote: str, hint: int | None) -> int | None:
    """1-based page holding the quote (hint first; a quote may run across a page break)."""
    q = norm(quote)
    if len(q) < 12:
        return None
    order = list(range(len(t.pages)))
    if hint and 1 <= hint <= len(t.pages):
        order.remove(hint - 1)
        order.insert(0, hint - 1)
    for i in order:
        hay = norm(t.pages[i] + (" " + t.pages[i + 1][:1500] if i + 1 < len(t.pages) else ""))
        if q in hay:
            return i + 1
    return None


# ---------------------------------------------------------------- fetch
_DATE_RE = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(20\d\d)", re.I)


def _call_date(pages: list[str], label: str) -> date:
    head = " ".join(pages[:3])
    m = _DATE_RE.search(head)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date()
        except ValueError:
            pass
    ym = parse_month_label(label)
    return date(ym[0], ym[1], 15) if ym else date.today()


def results_quarter(call: date) -> str:
    """The quarter a call discusses: the last quarter that ended at least ~10 days before
    the call (a 7 Aug call discusses Apr-Jun = FY27Q1)."""
    d = call - timedelta(days=10)
    # quarter-end months: Mar, Jun, Sep, Dec
    qm = ((d.month - 1) // 3) * 3  # start month index of d's calendar quarter
    end_month, end_year = (qm if qm else 12), (d.year if qm else d.year - 1)
    fy = end_year + 1 if end_month > 3 else end_year
    q = {6: 1, 9: 2, 12: 3, 3: 4}[end_month]
    return f"FY{fy % 100:02d}Q{q}"


def _pdf_pages(data: bytes) -> list[str]:
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(data))
    out = []
    for p in r.pages:
        t = p.extract_text() or ""
        t = re.sub(r"[ \t]+", " ", t)
        out.append(t)
    return out


def load(f: Fetcher | None, symbol: str, fund: dict, n: int = N_CALLS) -> tuple[list[Transcript], list[str]]:
    """Latest n transcripts, newest first. Normalised pages are stored in
    data/processed/transcripts/<SYM>.json so a rebuild works offline."""
    notes: list[str] = []
    STORE.mkdir(parents=True, exist_ok=True)
    path = STORE / f"{symbol}.json"
    stored = {}
    if path.exists():
        stored = {d["url"]: d for d in json.loads(path.read_text(encoding="utf-8"))}
    wanted = [c for c in fund.get("concalls", []) if c.get("transcript_url")][:n]
    if not wanted:
        notes.append("screener lists no concall transcripts")
    out: list[Transcript] = []
    for c in wanted:
        url = c["transcript_url"]
        if url in stored:
            d = stored[url]
            out.append(Transcript(symbol, d["label"], url, d["call_date"], d["quarter"], d["pages"]))
            continue
        if f is None:
            notes.append(f"{c['period_label']}: transcript not cached (offline)")
            continue
        try:
            pages = _pdf_pages(f.get_bytes(url, cache="forever"))
        except (FetchError, Exception) as e:  # noqa: BLE001 - fail soft, never crash
            notes.append(f"{c['period_label']}: transcript unavailable ({type(e).__name__})")
            continue
        if sum(len(p) for p in pages) < 2000:
            notes.append(f"{c['period_label']}: transcript is a scan with no text layer")
            continue
        cd = _call_date(pages, c["period_label"])
        out.append(Transcript(symbol, c["period_label"], url, cd.isoformat(), results_quarter(cd), pages))
    out.sort(key=lambda t: t.call_date, reverse=True)
    keep = {t.url: t.__dict__ for t in out}
    for url, d in stored.items():   # a 1-call read must not wipe the 4 calls already stored
        keep.setdefault(url, d)
    path.write_text(json.dumps(sorted(keep.values(), key=lambda d: d["call_date"], reverse=True), ensure_ascii=True), encoding="utf-8")
    return out, notes


# ---------------------------------------------------------------- passages for the model
_SPEAKER = re.compile(r"(?m)^\s*([A-Z][A-Za-z.\- ]{2,40}?)\s*:\s")
_FORWARD = re.compile(
    r"\b(guid\w*|outlook|expect\w*|target\w*|aspir\w*|aim\w*|going forward|going ahead|next (year|quarter|few|two|three|\d)|"
    r"this (fiscal|year|financial year)|full[- ]year|for the year|rest of the year|second half|h2|fy\s?'?2?\d\d|"
    r"medium[- ]term|long[- ]term|we (will|would|should|plan|intend|hope|are confident|remain confident|continue to)|"
    r"should (be|come|see)|likely to|anticipat\w*|plan to|in the range|band|centre of gravity|center of gravity|"
    r"pipeline|capex|commission\w*|launch\w*|open(ing)? \d+|add(ing)? \d+|by (march|fy|the end))\b", re.I)
_METRIC = re.compile(r"\b(revenue|sales|growth|margin|ebit\w*|profit|pat|volume|stores?|capex|debt|cash|attrition|"
                     r"utili[sz]ation|deal|tcv|order|capacity|share|realisation|realization|cost)\b", re.I)
_NUMBER = re.compile(r"\d|double[- ]digit|single[- ]digit|teens|twenties")
_PAST = re.compile(r"\b(grew|grown|was|were|came in|delivered|reported|stood|declined|increased|rose|fell|at)\b", re.I)


def _sentences(text: str) -> list[str]:
    text = re.sub(r"\s*\n\s*", " ", text)
    # don't split on decimals or common abbreviations
    parts = re.split(r"(?<=[.?!])\s+(?=[A-Z\"“])", text)
    return [p.strip() for p in parts if p.strip()]


def passages(t: Transcript, cap_chars: int = 16000) -> str:
    """Page-tagged excerpts of management speech: forward-looking sentences (with one
    sentence of context) and metric + number statements (actuals). Analyst questions
    and the moderator are dropped."""
    out: list[str] = []
    used = 0
    speaker = ""
    for pi, page in enumerate(t.pages, start=1):
        chunks = _SPEAKER.split(page)
        # split yields [pre, name, text, name, text, ...]
        seq = [(speaker, chunks[0])] + [(chunks[i], chunks[i + 1]) for i in range(1, len(chunks) - 1, 2)]
        for spk, text in seq:
            spk = spk.strip() or speaker
            speaker = spk
            if not text.strip() or re.match(r"moderator|operator", spk, re.I):
                continue
            sents = _sentences(text)
            if sents and sum(s.endswith("?") for s in sents) >= max(1, len(sents) // 2):
                continue  # an analyst's question
            keep: set[int] = set()
            for i, s in enumerate(sents):
                fwd = _FORWARD.search(s) and (_METRIC.search(s) or _NUMBER.search(s))
                act = _METRIC.search(s) and _NUMBER.search(s) and _PAST.search(s)
                if fwd or act:
                    keep.update({i, i + 1} if fwd else {i})
            if not keep:
                continue
            body = " ".join(sents[i] for i in sorted(keep) if i < len(sents))
            line = f"[p.{pi}] {spk}: {body}"
            if used + len(line) > cap_chars:
                return "\n".join(out)
            out.append(line)
            used += len(line)
    return "\n".join(out)
