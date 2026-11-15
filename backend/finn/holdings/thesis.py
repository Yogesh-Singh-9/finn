"""Thesis per holding, and which driver each event touches.

seed(): flash-lite reads the latest concall's management passages and names 2-3 drivers
of the investment case, each with a verbatim quote (checked on its page, else dropped),
plus the one forward-looking number to watch next quarter. When the promise ledger has a
pending numeric target, that wins over the model's pick (it's already graded in Python).

tag(): flash-lite says, per event, which driver it touches and whether it supports or
challenges it. The score's thesis part is then a fixed table: challenges +15, supports +5.
The user can edit the thesis in the app (localStorage); tags stay against the seeded one,
and the UI says so."""
from __future__ import annotations

import json
import logging
from typing import Literal

from pydantic import BaseModel

from .. import config, llm
from ..models import Cite, Driver, Event, Ledger, ScorePart, Thesis, ThesisTag
from ..research import transcripts as T

log = logging.getLogger("finn.thesis")
SEED_VERSION = "thesis-seed-v2"
TAG_VERSION = "thesis-tag-v2"
THESIS_POINTS = {"challenges": 15, "supports": 5, "neutral": 0}
STORE = config.PROCESSED_DIR / "theses.json"

_SEED_SYS = (
    "You read excerpts of an Indian listed company's latest earnings call (each line starts with [p.N], the PDF page, "
    "and the speaker). Answer only from the provided excerpts; copy quotes exactly; no outside knowledge; no calculations.\n"
    "1. drivers: the 3 things management says will drive the business over the next few years (the reasons a "
    "long-term investor would own it). Each: text (max 8 words, plain, e.g. 'Jewellery share gains in smaller towns'), "
    "why (max 18 words), quote (one sentence copied exactly), page.\n"
    "2. watch: the single most important forward-looking target or expectation for the coming quarters, with a number "
    "if management gave one: text (max 14 words, as said), quote (exact sentence), page. Null if none.\n"
    "Return {\"drivers\": [...], \"watch\": {...} or null}."
)
_S = {"type": "STRING"}
_SEED_SCHEMA = {"type": "OBJECT", "properties": {
    "drivers": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "text": _S, "why": _S, "quote": _S, "page": {"type": "INTEGER"}}, "required": ["text", "quote", "page"]}},
    "watch": {"type": "OBJECT", "nullable": True, "properties": {"text": _S, "quote": _S, "page": {"type": "INTEGER"}},
              "required": ["text", "quote", "page"]}},
    "required": ["drivers"]}


class _D(BaseModel):
    text: str
    why: str | None = None
    quote: str
    page: int = 0


class _W(BaseModel):
    text: str
    quote: str
    page: int = 0


class _Seed(BaseModel):
    drivers: list[_D] = []
    watch: _W | None = None


def _cite(t: T.Transcript, page: int, quote: str) -> Cite:
    return Cite(label=f"{t.call_label} p.{page}", doc_id=f"{t.url}#page={page}", page=page, quote=quote)


def _ledger_watch(symbol: str) -> tuple[str, Cite] | None:
    path = config.PROCESSED_DIR / "promises" / f"{symbol}.json"
    if not path.exists():
        return None
    try:
        led = Ledger.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    cands = [p for p in led.promises if p.kind == "numeric" and p.grade == "pending" and p.target_text]
    if not cands:
        return None
    latest = max(p.call_date for p in cands)
    rank = {"quarter": 0, "half": 1, "year": 2, "unspecified": 3, "multi_year": 4}
    p = sorted([c for c in cands if c.call_date == latest], key=lambda c: rank[c.horizon_kind])[0]
    seg = f" ({p.segment})" if p.segment and p.segment.lower() not in p.metric.lower() else ""
    hz = f" for {p.horizon}" if p.horizon else ""
    txt = f"{p.metric[:1].upper()}{p.metric[1:]}{seg} guided {p.target_text}{hz}"
    if p.progress:
        txt += f"; {p.progress.split(' vs ')[0].replace(' (company, on the call)', '')}"
    return txt, p.source


def seed(symbol: str, fund: dict | None, f, use_llm: bool = True) -> Thesis | None:
    notes: list[str] = []
    if not fund:
        return None
    tr, tnotes = T.load(f, symbol, fund, n=2)
    notes += tnotes
    lw = _ledger_watch(symbol)
    if not tr:
        return Thesis(symbol=symbol, drivers=[], line="No concall transcript on screener; write your own thesis.",
                      watch=f"Watch next quarter: {lw[0]}" if lw else None, watch_source=lw[1] if lw else None, notes=notes)
    t = tr[0]
    drivers: list[Driver] = []
    watch, wsrc, by = None, None, None
    if use_llm and llm.available("cheap"):
        try:
            out, meta = llm.json_call_meta(tier="cheap", purpose="thesis_seed", prompt_version=SEED_VERSION, system=_SEED_SYS,
                                           user=f"Company: {fund.get('company', symbol)}. {t.call_label}, {t.call_date}.\n\n{T.passages(t, cap_chars=12000)}",
                                           schema=_SEED_SCHEMA, result_type=_Seed)
            by = f"{meta.provider}:{meta.model}"
            def locate(quote: str, page: int):
                for x in tr[:2]:
                    pg = T.find_page(x, quote, page)
                    if pg is not None:
                        return x, pg
                return None, None
            for d in out.drivers[:3]:
                x, pg = locate(d.quote, d.page)
                if pg is None:
                    notes.append(f"driver '{d.text}' dropped: quote not found in the transcripts")
                    continue
                drivers.append(Driver(text=d.text.strip().rstrip("."), why=d.why, source=_cite(x, pg, d.quote)))
            if out.watch:
                x, pg = locate(out.watch.quote, out.watch.page)
                if pg is not None and x is tr[0]:   # watch next quarter: only from the latest call
                    watch, wsrc = out.watch.text.strip().rstrip("."), _cite(x, pg, out.watch.quote)
        except llm.LLMUnavailable as e:
            notes.append(f"thesis seed unavailable ({str(e)[:80]})")
    if lw:   # the ledger's pending target is graded in Python: prefer it
        watch, wsrc = lw
    line = ("; ".join(d.text for d in drivers)) if drivers else "Write your thesis: what has to go right for you to keep owning it?"
    return Thesis(symbol=symbol, drivers=drivers, line=line, seeded_by=by, seeded_from=t.call_label,
                  watch=f"Watch next quarter: {watch}" if watch else None, watch_source=wsrc, notes=notes)


# ---------------------------------------------------------------- tagging events
_TAG_SYS = (
    "You link stock-exchange events to an investor's thesis. For each event you get the company's thesis drivers "
    "(numbered) and a one-line description of the event. Answer only from what is given; no outside knowledge. "
    "Return for each event: id; driver = the number of the driver it most directly touches, or null if it touches "
    "none (routine paperwork, compliance, unrelated); stance = supports (evidence the driver is working), challenges "
    "(evidence against it, a delay, a miss, a risk to it) or neutral; reason (max 15 words). "
    "Be strict: only results can be evidence for a margin, growth or profit driver; a single client win, product "
    "launch or press release touches a driver only if the driver is about that exact business, else driver null. "
    "Return {\"items\": [...]}."
)
_TAG_SCHEMA = {"type": "OBJECT", "properties": {"items": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "id": _S, "driver": {"type": "INTEGER", "nullable": True},
    "stance": {"type": "STRING", "enum": ["supports", "challenges", "neutral"]}, "reason": _S},
    "required": ["id", "stance"]}}}, "required": ["items"]}


class _Tag(BaseModel):
    id: str
    driver: int | None = None
    stance: Literal["supports", "challenges", "neutral"] = "neutral"
    reason: str = ""


def load_theses() -> dict[str, Thesis]:
    if not STORE.exists():
        return {}
    try:
        return {k: Thesis.model_validate(v) for k, v in json.loads(STORE.read_text(encoding="utf-8")).items()}
    except ValueError:
        return {}


def _describe(e: Event) -> str:
    if e.results_card:
        s = f"Results {e.results_card.period_label}: {e.results_card.headline}"
        for g in e.results_card.guidance:
            s += f"; {g.metric} guided {g.guided}, delivered {g.delivered} ({g.grade})"
        return s
    if e.mgmt_card:
        return f"Management change: {e.mgmt_card.short or e.mgmt_card.headline}"
    return f"{e.category.replace('_', ' ')}: {e.summary or e.title}"


def tag(events: list[Event], theses: dict[str, Thesis], use_llm: bool = True, min_score: float = 10) -> dict[str, int]:
    """Sets e.thesis_tag and rewrites the thesis score part, in place."""
    stats = {"tagged": 0, "supports": 0, "challenges": 0, "untouched": 0}
    todo = [e for e in events if theses.get(e.symbol) and theses[e.symbol].drivers and e.score >= min_score]
    if not todo or not use_llm or not llm.available("cheap"):
        return stats
    for i in range(0, len(todo), 15):
        batch = todo[i:i + 15]
        blocks = []
        for e in batch:
            th = theses[e.symbol]
            drv = " | ".join(f"{k + 1}. {d.text}" for k, d in enumerate(th.drivers))
            blocks.append(f"EVENT {e.id} · {e.symbol}\nDRIVERS: {drv}\nEVENT: {_describe(e)[:400]}")
        try:
            items, meta = llm.json_call_meta(tier="cheap", purpose="thesis_tag", prompt_version=TAG_VERSION, system=_TAG_SYS,
                                             user="\n\n".join(blocks), schema=_TAG_SCHEMA, result_type=list[_Tag], unwrap="items")
        except llm.LLMUnavailable as ex:
            log.warning("thesis tagging unavailable: %s", ex)
            return stats
        by = f"{meta.provider}:{meta.model}"
        got = {t.id: t for t in items}
        for e in batch:
            t = got.get(e.id)
            if not t:
                continue
            drivers = theses[e.symbol].drivers
            idx = (t.driver - 1) if t.driver and 1 <= t.driver <= len(drivers) else None
            stance = t.stance if idx is not None else "neutral"
            e.thesis_tag = ThesisTag(driver=idx, driver_text=drivers[idx].text if idx is not None else None,
                                     stance=stance, reason=t.reason, by=by)
            stats["tagged"] += 1
            stats[stance if stance != "neutral" else "untouched"] += 1
            pts = THESIS_POINTS[stance]
            note = (f"{stance} '{drivers[idx].text}': {t.reason}" if idx is not None and stance != "neutral"
                    else "touches no thesis driver" if idx is None else f"touches '{drivers[idx].text}', neutral")
            e.breakdown = [p for p in e.breakdown if p.part != "thesis"] + [ScorePart(part="thesis", points=pts, note=note)]
    return stats
