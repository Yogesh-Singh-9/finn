"""Management card: who changed, which role, which direction, from when, and where they
come from, read from the filing's own Reg 30 annexure / profile.

flash-lite extracts; Python verifies. Every name, employer, role, quote, date and background
line must be found in the filing text (after whitespace/punctuation normalisation), or it is
dropped. Employers the model offered that the text lacks are kept in `dropped`, so the
check is visible. No LinkedIn scraping: a people-search link only.
"""
from __future__ import annotations

import logging
import re
from typing import Literal
from urllib.parse import quote_plus

from pydantic import BaseModel

from .. import llm
from ..classify.pdftext import first_pages_text
from ..http import Fetcher
from ..models import MgmtCard, PersonChange, PriorRole

log = logging.getLogger("finn.cards.mgmt")
PROMPT_VERSION = "mgmt-v2"
MAX_CHARS = 24000

_SYSTEM = (
    "You extract management changes from an Indian stock-exchange filing. Answer only from the provided text; "
    "copy words exactly as written, never paraphrase, infer or use outside knowledge. "
    "List every person whose position at the company changes (appointed, retired, resigned, ceased, re-appointed, "
    "redesignated). Skip people who only sign the letter. For each: "
    "name (as written, without Mr./Ms./Dr.); role (the position taken or left, as written); change; "
    "effective (the date phrase as written, or null); reason (as written, or null); "
    "background (one sentence copied verbatim from their profile, or null); "
    "prior_roles: every organisation named in their profile that they worked at, led, advised or sat on the board "
    "of, past or current, other than this company, each with role as written (or null) "
    "and quote = the exact sentence it appears in. If there is no profile, prior_roles is []. "
    "Return {\"people\": [...]}."
)
_SCHEMA = {
    "type": "OBJECT",
    "properties": {"people": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {
            "name": {"type": "STRING"},
            "role": {"type": "STRING", "nullable": True},
            "change": {"type": "STRING", "enum": ["appointed", "resigned", "retired", "ceased", "re-appointed",
                                                   "redesignated", "other"]},
            "effective": {"type": "STRING", "nullable": True},
            "reason": {"type": "STRING", "nullable": True},
            "background": {"type": "STRING", "nullable": True},
            "prior_roles": {"type": "ARRAY", "items": {
                "type": "OBJECT",
                "properties": {"org": {"type": "STRING"}, "role": {"type": "STRING", "nullable": True},
                               "quote": {"type": "STRING"}},
                "required": ["org", "quote"]}},
        },
        "required": ["name", "change", "prior_roles"],
    }}},
    "required": ["people"],
}


class _LLMRole(BaseModel):
    org: str
    role: str | None = None
    quote: str = ""


class _LLMPerson(BaseModel):
    name: str
    role: str | None = None
    change: Literal["appointed", "resigned", "retired", "ceased", "re-appointed", "redesignated", "other"] = "other"
    effective: str | None = None
    reason: str | None = None
    background: str | None = None
    prior_roles: list[_LLMRole] = []


def _norm(s: str | None) -> str:
    s = (s or "").lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"\b(mr|ms|mrs|dr|shri|smt)\.?\s", " ", s)
    return re.sub(r"[^a-z0-9&+]+", " ", s).strip()


def _in(needle: str | None, hay_norm: str) -> bool:
    n = _norm(needle)
    return bool(n) and f" {n} " in f" {hay_norm} "


def _sentence_with(text: str, needle: str) -> str | None:
    flat = re.sub(r"\s+", " ", text)
    for sent in re.split(r"(?<=[a-z0-9)][.;])\s+(?=[A-Z])", flat):
        if _in(needle, _norm(sent)):
            return sent.strip()[:400]
    return None


def linkedin_search(name: str, hint: str | None) -> str:
    return "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(f"{name} {hint or ''}".strip())


def _verify(p: _LLMPerson, text: str, tnorm: str, company: str, source: dict[str, str]) -> PersonChange | None:
    if not _in(p.name, tnorm):
        return None
    kept: list[PriorRole] = []
    dropped: list[str] = []
    company_n = _norm(re.sub(r"\b(limited|ltd)\b\.?", "", company, flags=re.I))
    for r in p.prior_roles:
        if not _in(r.org, tnorm) or (company_n and _norm(r.org) == company_n):
            dropped.append(r.org)
            continue
        quote = r.quote if _in(r.quote, tnorm) else _sentence_with(text, r.org)
        kept.append(PriorRole(org=r.org, role=r.role if _in(r.role, tnorm) else None, quote=quote or r.org))
    hint = kept[0].org if kept and p.change == "appointed" else company
    return PersonChange(
        name=p.name, role=p.role if _in(p.role, tnorm) else None, change=p.change,
        effective=p.effective if _in(p.effective, tnorm) else None,
        reason=p.reason if _in(p.reason, tnorm) else None,
        background=p.background if _in(p.background, tnorm) else None,
        prior_roles=kept, dropped=dropped, linkedin_search=linkedin_search(p.name, hint), source=source,
    )


_CHANGE_FROM_SUBTYPE = {"exit": "ceased", "appoint": "appointed"}


def _fallback(people: list[str], subtypes: list[str | None], company: str, source: dict[str, str]) -> list[PersonChange]:
    out = []
    for i, name in enumerate(people):
        sub = subtypes[i] if i < len(subtypes) else None
        change = "other"
        role = None
        if sub:
            role, _, direction = sub.rpartition("_")
            change = _CHANGE_FROM_SUBTYPE.get(direction, "re-appointed" if sub == "reappointment" else "other")
            role = role.replace("_", " ") or None
        out.append(PersonChange(name=name, role=role, change=change, effective=None,  # type: ignore[arg-type]
                                linkedin_search=linkedin_search(name, company), source=source))
    return out


_VERB = {"appointed": "appointed", "resigned": "resigns", "retired": "retires", "ceased": "steps down",
         "re-appointed": "re-appointed", "redesignated": "redesignated", "other": "changes role"}


_TOP_ROLES = [("Chairman", re.compile(r"^(?!.*(vice|emeritus|deputy)).*\bchair(man|person)?\b", re.I)),
              ("Vice-chairman", re.compile(r"vice[- ]?chair", re.I)),
              ("CEO", re.compile(r"\bceo\b(?!\s*\()|chief executive officer(?!\s*\()", re.I)),
              ("MD", re.compile(r"(?<!deputy )managing director", re.I)),
              ("CFO", re.compile(r"\bcfo\b|chief financial officer", re.I)),
              ("Deputy MD", re.compile(r"deputy managing director", re.I)),
              ("Non-executive director", re.compile(r"non[- ]executive", re.I)),
              ("Executive director", re.compile(r"executive director|whole[- ]time director", re.I)),
              ("Independent director", re.compile(r"(?<!non-)(?<!non )\bindependent director", re.I)),
              ("Director", re.compile(r"\bdirector\b", re.I))]
_OUT = {"resigned", "retired", "ceased"}


def _short_name(name: str) -> str:
    """'Noel N. Tata' -> 'Noel Tata' (drop middle initials, phone form only)."""
    toks = name.replace(".", ". ").split()
    if not toks or len(toks[0].rstrip(".")) <= 1:
        return name  # initials-first names ("V. N. Srivatsan") stay as written
    keep = [t for i, t in enumerate(toks) if i in (0, len(toks) - 1) or len(t.rstrip(".")) > 1]
    return " ".join(keep)


def _short_role(role: str | None) -> str | None:
    r = re.split(r",| – | - | and a ", re.sub(r"\s+", " ", role or "").strip())[0].strip()
    return (r if len(r) <= 34 else r[:33].rsplit(" ", 1)[0] + "…") or None


def _role_key(role: str | None) -> tuple[int, str] | None:
    for i, (label, rx) in enumerate(_TOP_ROLES):
        if role and rx.search(role):
            return i, label
    return None


def short_headline(card: MgmtCard) -> str:
    """Phone headline, built in code. A succession (someone leaves a role, someone takes
    it) reads 'Chairman: Noel Tata → Bahram Vakil'; otherwise the most senior change."""
    if not card.people:
        return "No change of position (correction)"
    keyed = [(k, p) for p in card.people if (k := _role_key(p.role))]
    names = {_norm(p.name) for p in card.people}
    more = len(names) - 1
    for (rank, label), p in sorted(keyed, key=lambda t: t[0][0]):
        if p.change in _OUT:
            succ = next((q for (r2, _), q in keyed if r2 == rank and q.change == "appointed"
                         and _norm(q.name) != _norm(p.name)), None)
            if succ:
                extra = len(names - {_norm(p.name), _norm(succ.name)})
                return (f"{label}: {_short_name(p.name)} → {_short_name(succ.name)}"
                        + (f" · +{extra} more" if extra else ""))
    if keyed:
        (rank, label), p = min(keyed, key=lambda t: (t[0][0], t[1].change not in _OUT))
    else:
        p, label = card.people[0], None
    verb = {"appointed": "joins", "re-appointed": "re-appointed", "redesignated": "redesignated",
            "resigned": "resigns", "retired": "retires", "ceased": "exits", "other": "changes"}[p.change]
    role = label or _short_role(p.role) or "Management"
    return f"{role}: {_short_name(p.name)} {verb}" + (f" · +{more} more" if more > 0 else "")


def _headline(people: list[PersonChange]) -> str:
    """One clause per person (their first change)."""
    seen: dict[str, PersonChange] = {}
    for p in people:
        seen.setdefault(_norm(p.name), p)
    bits = []
    for p in list(seen.values())[:3]:
        # Keep parentheticals: "CEO (Helicopter Complex)" is not the company CEO.
        role = re.sub(r"\s+", " ", p.role or "").strip()
        role = role if len(role) <= 60 else role[:57].rsplit(" ", 1)[0] + "…"
        bits.append(f"{p.name} {_VERB[p.change]}" + (f" as {role}" if role else ""))
    more = len(seen) - 3
    return "; ".join(bits) + (f"; +{more} more" if more > 0 else "") if bits else "Management change"


def build_card(symbol: str, company: str, filings: list[tuple[str | None, str | None]], people_hint: list[str],
               fetcher: Fetcher, use_llm: bool = True) -> MgmtCard | None:
    """filings: (attachment_url, subtype) per filing in the event."""
    urls = list(dict.fromkeys(u for u, _ in filings if u))
    texts = []
    for u in urls:
        t = first_pages_text(fetcher, u, pages=8, max_chars=MAX_CHARS)
        if t:
            texts.append(t)
    text = "\n\n".join(texts)[:MAX_CHARS]
    source = {"doc_id": urls[0] if urls else "", "section": "Reg 30 annexure / profiles"}
    subtypes = [s for _, s in filings]
    if not text:
        people = _fallback(people_hint, subtypes, company, source)
        return MgmtCard(symbol=symbol, headline=_headline(people), people=people,
                        extracted_by="regex (filing PDF unreadable)", filing_urls=urls) if people else None
    if use_llm and llm.available("cheap"):
        try:
            got, meta = llm.json_call_meta(tier="cheap", purpose="mgmt_card", prompt_version=PROMPT_VERSION,
                                           system=_SYSTEM, user=f"Company: {company}\n\n{text}", schema=_SCHEMA,
                                           result_type=list[_LLMPerson], unwrap="people")
            tnorm = _norm(text)
            by = (f"{meta.provider}:{meta.model}" + (" (cached)" if meta.cached else "")
                  + (" (fallback)" if meta.fallback else ""))
            if not got:
                # The model read the whole letter and found no change of position (e.g. a
                # correction to an earlier filing). Say so; don't fall back to a guess.
                return MgmtCard(symbol=symbol, people=[], extracted_by=by, filing_urls=urls,
                                headline="No change of position in this filing (correction or clarification)")
            people = [v for p in got if (v := _verify(p, text, tnorm, company, source))]
            if people:
                return MgmtCard(symbol=symbol, headline=_headline(people), people=people, extracted_by=by,
                                filing_urls=urls)
            reason = "regex (model's names not found in the filing)"
        except llm.LLMUnavailable as e:
            log.warning("mgmt card LLM unavailable for %s: %s", symbol, e)
            reason = "regex (LLM unavailable)"
    else:
        reason = "regex (LLM disabled)"
    people = _fallback(people_hint, subtypes, company, source)
    return MgmtCard(symbol=symbol, headline=_headline(people), people=people,
                    extracted_by=reason, filing_urls=urls) if people else None
