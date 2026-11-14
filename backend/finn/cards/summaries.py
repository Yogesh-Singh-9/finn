"""One-line summaries for events whose title is a raw cover-letter line ("The Special
Committee of the Board of Directors at its 2nd meeting held on ..."). needs_you and glance
only; set-aside keeps its raw title. Results and management titles are built in code and
are never summarised.

flash-lite, batched 20 per call, cached. A summary is rejected if it carries any number
that is not in its source text (the model may not invent or compute figures).
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel

from .. import llm
from ..classify.pdftext import first_pages_text
from ..http import Fetcher
from ..models import Event

log = logging.getLogger("finn.cards.summaries")
PROMPT_VERSION = "sum-v3"
BATCH = 20
SKIP = {("results", "outcome")}
SKIP_CATS = {"management_change"}

_SYSTEM = (
    "You write one-line summaries of Indian stock-exchange filings for an investor's feed. "
    "Answer only from the provided excerpt. Each summary: at most 18 words, plain English, starts with what "
    "happened, headline style without a leading 'The' (e.g. 'QIP allotment approved: 7,683,981 shares at INR 5,550'), "
    "about the given event type only (the letter may cover other items). Copy numbers exactly as written in "
    "the excerpt; never compute, convert or round them. No boilerplate (Regulation 30, 'informed the exchange'). "
    "If the excerpt says nothing useful, return the summary 'not disclosed'. "
    "Return {\"items\": [{\"id\": ..., \"summary\": ...}]} with one item per input id."
)
_SCHEMA = {
    "type": "OBJECT",
    "properties": {"items": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {"id": {"type": "STRING"}, "summary": {"type": "STRING"}},
        "required": ["id", "summary"]}}},
    "required": ["items"],
}
_BOILER = re.compile(r"^(pursuant|in terms|in continuation|intimation|submission|disclosure|outcome of|update|the |this |we |"
                     r"general updates|updates?$|press release$|clarification)", re.I)
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


class _Item(BaseModel):
    id: str
    summary: str


def _nums(s: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(s)}


def numbers_grounded(summary: str, source: str) -> list[str]:
    """Numbers in the summary that the source doesn't contain (commas ignored)."""
    src = _nums(source)
    return sorted(n for n in _nums(summary) if n not in src)


def is_raw(title: str, nse_subjects: set[str]) -> bool:
    """A cover-letter line rather than a headline: the generic NSE subject itself, a
    boilerplate opener, or a long sentence lifted from the letter."""
    t = title.strip()
    return t in nse_subjects or bool(_BOILER.match(t)) or len(t) > 70 or "·" in t


def needs_summary(e: Event) -> bool:
    return (e.tier in ("needs_you", "glance") and (e.category, e.subtype) not in SKIP
            and e.category not in SKIP_CATS and is_raw(e.title, {f.nse_subject for f in e.filings}))


def summarise(events: list[Event], fetcher: Fetcher, use_llm: bool = True) -> dict[str, int]:
    """Fills e.summary / e.summary_by in place. Returns counts for the log."""
    todo = [e for e in events if needs_summary(e)]
    stats = {"eligible": len(todo), "summarised": 0, "rejected": 0, "failed": 0}
    if not todo or not use_llm or not llm.available("cheap"):
        return stats
    sources: dict[str, str] = {}
    for e in todo:
        latest = max(e.filings, key=lambda f: f.filed_at)
        pdf = first_pages_text(fetcher, latest.url, pages=2, max_chars=2500) or ""
        titles = " | ".join(dict.fromkeys(f.title for f in e.filings))[:600]
        kind = e.category.replace("_", " ") + (f" / {e.subtype.replace('_', ' ')}" if e.subtype else "")
        sources[e.id] = f"{e.company} ({e.symbol}). Event type: {kind}. Filings: {titles}\n{pdf}"
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        user = "\n\n".join(f"id={e.id}\n{sources[e.id][:1800]}" for e in chunk)
        try:
            items, meta = llm.json_call_meta(tier="cheap", purpose="summaries", prompt_version=PROMPT_VERSION,
                                             system=_SYSTEM, user=user, schema=_SCHEMA, result_type=list[_Item],
                                             unwrap="items")
        except llm.LLMUnavailable as ex:
            log.warning("summaries unavailable: %s", ex)
            stats["failed"] += len(chunk)
            continue
        by = {it.id: it.summary.strip() for it in items}
        model = f"{meta.provider}:{meta.model}"
        for e in chunk:
            s = by.get(e.id)
            if not s or s.lower().startswith("not disclosed"):
                stats["failed"] += 1
                continue
            bad = numbers_grounded(s, sources[e.id][:1800])
            if bad:
                e.summary_by = f"rejected ({model}): {', '.join(bad)} not in the filing"
                stats["rejected"] += 1
                continue
            e.summary, e.summary_by = s.rstrip("."), model
            stats["summarised"] += 1
    return stats
