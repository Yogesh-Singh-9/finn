"""Morning Call: a 60-90 second spoken run through what needs you this week.

Python picks the items (needs_you, last 7 days, by score) and writes each item's facts
as text; the strong model only turns those facts into speech. Every sentence is then
checked: a number that isn't in that chapter's facts drops the sentence (the model never
does arithmetic or adds numbers). Without a model, a template built from the same facts
is used, so the play button always has something to say. Cached by the honest LLM cache:
same brief -> same prompt -> no new call."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta

from pydantic import BaseModel

from .. import config, llm
from ..models import BriefSnapshot, CallChapter, Event, MorningCall

log = logging.getLogger("finn.morning")
PROMPT_VERSION = "morning-v3"
MAX_ITEMS = 5
WPM = 150

_SYS = (
    "You write a short spoken morning briefing for one investor about their own portfolio, read aloud by a "
    "text-to-speech voice. Use only the facts given for each item; no outside knowledge, no advice, no predictions, "
    "no calculations. Write numbers in digits exactly as they appear in the facts (e.g. 22%, 257 bps); never introduce "
    "a number that is not in that item's facts and never spell numbers as words. Plain, calm, conversational English, "
    "short sentences (max 22 words). Say the company name, not the ticker symbol.\n"
    "Return {\"intro\": one or two sentences, \"items\": [{\"id\": item id, \"text\": 2 to 3 sentences}], "
    "\"outro\": one sentence}. Keep the whole thing between 150 and 220 words. Lead each item with why it matters "
    "to the thesis or the promise when the facts say so."
)
_S = {"type": "STRING"}
_SCHEMA = {"type": "OBJECT", "properties": {
    "intro": _S, "outro": _S,
    "items": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"id": _S, "text": _S}, "required": ["id", "text"]}}},
    "required": ["intro", "items", "outro"]}


class _Item(BaseModel):
    id: str
    text: str


class _Out(BaseModel):
    intro: str
    items: list[_Item]
    outro: str = ""


_SUFFIX = re.compile(r"\s+(company\s+)?(limited|ltd\.?)$|\s+india\s+limited$", re.I)


def short_name(company: str) -> str:
    return _SUFFIX.sub("", company.strip()).strip()


def spoken(s: str) -> str:
    """Code-built headlines in words a voice can read; digits unchanged (the number check
    compares digits)."""
    s = s.replace("\u2212", "-").replace("\u2013", "-")
    s = re.sub(r"\u20b9\s?([\d,.]+)\s?cr\b", r"\1 crore rupees", s)
    s = re.sub(r"(?<![\w.])\+(\d)", r"up \1", s)
    s = re.sub(r"(?<![\w.])-(\d)", r"down \1", s)
    for a, b in (("YoY", "year on year"), ("QoQ", "quarter on quarter"), ("bps", "basis points"), ("PAT", "profit after tax"),
                 ("PBT", "profit before tax"), ("NII", "net interest income"), ("rep.", "reported")):
        s = re.sub(r"\b" + re.escape(a) + r"(?=\W|$)", b, s)
    return s


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def _title(e: Event) -> str:
    if e.results_card:
        return f"{e.symbol} · {e.results_card.period_label} results"
    if e.mgmt_card:
        return f"{e.symbol} · management change"
    return f"{e.symbol} · {e.category.replace('_', ' ')}"


def facts(e: Event) -> str:
    """Everything the model may say about this event, as text. Numbers come from code."""
    out = [f"Company: {short_name(e.company)}."]
    if e.results_card:
        c = e.results_card
        out.append(f"{c.period_label} results: {c.headline}.")
        if c.reported_headline:
            out.append(f"As reported, before one-offs: {c.reported_headline}.")
        for g in c.guidance:
            flag = {"on_track": "on track", "behind": "behind"}.get(g.progress_flag or "", "")
            out.append(f"Promise check: {g.metric} guided {g.guided} on the {g.call_label or 'last call'}, "
                       f"delivered {g.delivered}: {flag or g.grade.replace('_', ' ')}.")
    elif e.mgmt_card:
        out.append(f"Management change: {e.mgmt_card.short or e.mgmt_card.headline}.")
    else:
        out.append(f"{e.category.replace('_', ' ').capitalize()}: {e.summary or e.title}.")
    if e.thesis_tag and e.thesis_tag.stance != "neutral" and e.thesis_tag.driver_text:
        out.append(f"Thesis: this {e.thesis_tag.stance} the driver '{e.thesis_tag.driver_text}' ({e.thesis_tag.reason}).")
    if e.reaction:
        r = e.reaction
        out.append(f"Stock next session: {_pct(r.ret)}, {_pct(r.abn_ret)} versus the NIFTY.")
    return spoken(" ".join(out))


# ---------------------------------------------------------------- number check
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WORDNUM = re.compile(r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|lakh|crore|million|billion)\b", re.I)


def _nums(s: str) -> set[str]:
    return {m.group(0).replace(",", "").rstrip(".") for m in _NUM.finditer(s)}


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.?!])\s+(?=[A-Z₹\"'])", re.sub(r"\s+", " ", text).strip())
    return [p.strip() for p in parts if p.strip()]


def _check(text: str, allowed: str, dropped: list[str]) -> list[str]:
    ok_nums = _nums(allowed)
    keep = []
    for s in _sentences(text):
        bad = [n for n in _nums(s) if n not in ok_nums]
        # "crore" is fine when it's next to a digit that passed; spelled-out numbers aren't
        spelled = [w for w in _WORDNUM.findall(s) if w.lower() not in ("crore", "lakh") or not _NUM.search(s)]
        if bad or spelled:
            dropped.append(s)
            continue
        keep.append(s)
    return keep


# ---------------------------------------------------------------- build
def pick(brief: BriefSnapshot) -> tuple[list[Event], int, str]:
    end = brief.window_to
    since = end - timedelta(days=7)
    recent = [e for e in brief.events if e.occurred_at.date() >= since]
    needs = sorted([e for e in recent if e.tier == "needs_you"], key=lambda e: -e.score)
    glance = sum(e.tier == "glance" for e in recent)
    return needs[:MAX_ITEMS], glance + max(0, len(needs) - MAX_ITEMS), f"last 7 days to {end:%d %b}"


def _template(items: list[Event], rest: int) -> tuple[str, dict[str, str], str]:
    intro = f"Good morning. {len(items)} things need you this week." if items else "Good morning. Nothing needs you this week."
    per = {}
    for e in items:
        f = facts(e)
        parts = [s for s in _sentences(f) if not s.startswith("Company:")][:3]
        per[e.id] = f"{short_name(e.company)}. " + " ".join(parts)
    outro = (f"{rest} more are worth a glance in the feed." if rest else "That's everything.")
    return intro, per, outro


def build(brief: BriefSnapshot, use_llm: bool = True) -> MorningCall:
    llm.reset_usage()
    items, rest, window = pick(brief)
    allowed = {e.id: facts(e) for e in items}
    head = f"{len(items)} items need you; {rest} more are worth a glance. Window: {window}."
    dropped: list[str] = []
    by = "template (no model)"
    intro, per, outro = _template(items, rest)
    if use_llm and items:
        user = head + "\n\n" + "\n\n".join(f"ITEM {e.id}\n{allowed[e.id]}" for e in items)
        for tier in ("strong", "cheap"):
            if not llm.available(tier):
                continue
            try:
                out, meta = llm.json_call_meta(tier=tier, purpose="morning_call", prompt_version=PROMPT_VERSION,
                                               system=_SYS, user=user, schema=_SCHEMA, result_type=_Out)
            except llm.LLMUnavailable as ex:
                log.warning("morning call: %s chain unavailable (%s)", tier, ex)
                continue
            by = f"{meta.provider}:{meta.model}"
            got = {i.id: i.text for i in out.items}
            intro = " ".join(_check(out.intro, head, dropped)) or intro
            outro = " ".join(_check(out.outro, head, dropped)) or outro
            for e in items:
                kept = _check(got.get(e.id, ""), allowed[e.id], dropped)
                if kept:
                    per[e.id] = " ".join(kept)
                # else keep the template line for that item: never silent
            break
    chapters = [CallChapter(event_id=None, symbol=None, title="Intro", sentences=_sentences(intro))]
    chapters += [CallChapter(event_id=e.id, symbol=e.symbol, title=_title(e), sentences=_sentences(per[e.id])) for e in items]
    chapters.append(CallChapter(event_id=None, symbol=None, title="Close", sentences=_sentences(outro)))
    words = sum(len(s.split()) for c in chapters for s in c.sentences)
    return MorningCall(brief_generated_at=brief.generated_at, generated_at=datetime.now(), window=window,
                       chapters=chapters, words=words, est_seconds=round(words / WPM * 60), by=by,
                       dropped=dropped, llm_usage=llm.USAGE.line())


def write(call: MorningCall):
    out = config.SNAPSHOT_DIR / "morning.json"
    out.write_text(json.dumps(call.model_dump(mode="json"), indent=1, ensure_ascii=True), encoding="utf-8")
    return out
