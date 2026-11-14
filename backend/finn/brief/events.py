"""Fold classified filings into events. An investor thinks in events, not filings:

- identical filings (same symbol, subject, text, minute) -> one
- results day: 'Outcome of Board Meeting' (results) + same-day deck / results press
  release / call notice / dividend -> one results event per symbol + date
- management filings with the same symbol + timestamp (TRENT 29 Oct 16:32: chairman
  retirement + 2 appointments + director change) -> one event, dominant subtype wins
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..classify.taxonomy import MGMT_RANK
from ..models import ClassifiedFiling, ResultsFilingRef, ResultsRef

RESULTS_FOLD = {
    ("results", "outcome"), ("results", "press_release"), ("results", "shareholder_letter"),
    ("results", "auditor_report"), ("investor_meetings", "deck"), ("earnings_call", "call_notice"),
}
FOLD_PART_LABEL = {
    ("results", "press_release"): "press release", ("results", "shareholder_letter"): "shareholder letter",
    ("results", "auditor_report"): "auditor report", ("investor_meetings", "deck"): "deck",
    ("earnings_call", "call_notice"): "call notice", ("dividend", None): "dividend",
}
STORY_CATS = {"fund_raise", "M&A", "litigation", "credit_rating", "press_release"}
STORY_DAYS = 7
MGMT_LABEL = {
    "chairman_exit": "Chairman exit", "ceo_md_exit": "CEO/MD exit", "cfo_exit": "CFO exit",
    "vice_chairman_exit": "Vice-chairman exit", "ceo_md_appoint": "New CEO/MD", "cfo_appoint": "New CFO",
    "chairman_appoint": "New chairman", "executive_director_exit": "Executive director exit",
    "executive_director_appoint": "New executive director", "senior_mgmt_exit": "Senior management exit",
    "director_exit": "Director exit", "nonexec_director_exit": "Non-executive director exit",
    "senior_mgmt_appoint": "Senior management appointment", "vice_chairman_appoint": "New vice-chairman",
    "director_appoint": "New director", "nonexec_director_appoint": "New non-executive director",
    "independent_director_exit": "Independent director exit",
    "independent_director_appoint": "New independent director", "reappointment": "Re-appointment",
}


@dataclass
class EventDraft:
    symbol: str
    company: str
    category: str
    subtype: str | None
    title: str
    filings: list[ClassifiedFiling]
    duplicates: int = 0
    results_ref: ResultsRef | None = None
    people: list[str] = field(default_factory=list)

    @property
    def occurred_at(self) -> datetime:
        return min(f.filed_at for f in self.filings)

    @property
    def latest_at(self) -> datetime:
        return max(f.filed_at for f in self.filings)

    @property
    def uncertain(self) -> bool:
        return any(f.classified_by == "default" for f in self.filings)

    @property
    def amount(self) -> tuple[float | None, str | None]:
        best: tuple[float | None, str | None] = (None, None)
        for f in self.filings:
            if f.amounts_cr and (best[0] is None or f.amounts_cr[0] > best[0]):
                best = (f.amounts_cr[0], f.amount_note)
        return best


def _quarter_label(key: str | None) -> str | None:
    return f"{key[4:]} {key[:4]}" if key and len(key) == 6 else None  # FY27Q2 -> "Q2 FY27"


def _match_results_ref(refs: list[ResultsFilingRef], day: date) -> ResultsRef | None:
    for r in refs:
        if r.broadcast_at and day <= r.broadcast_at.date() <= day + timedelta(days=1) and r.consolidated:
            return ResultsRef(period_key=r.period_key, xbrl_url=r.xbrl_url, consolidated=r.consolidated)
    for r in refs:
        if r.broadcast_at and day <= r.broadcast_at.date() <= day + timedelta(days=1):
            return ResultsRef(period_key=r.period_key, xbrl_url=r.xbrl_url, consolidated=r.consolidated)
    return None


def fold(filings: list[ClassifiedFiling], results_refs: dict[str, list[ResultsFilingRef]]) -> list[EventDraft]:
    filings = sorted(filings, key=lambda f: f.filed_at)

    # 1. exact duplicates
    seen: dict[tuple, int] = {}
    uniq: list[ClassifiedFiling] = []
    dup_count: dict[str, int] = defaultdict(int)
    for f in filings:
        key = (f.symbol, f.nse_subject, f.text.strip().lower(), f.filed_at.replace(second=0))
        if key in seen:
            dup_count[uniq[seen[key]].id] += 1
            continue
        seen[key] = len(uniq)
        uniq.append(f)

    used: set[str] = set()
    events: list[EventDraft] = []

    # 2. results days
    anchors = [f for f in uniq if (f.category, f.subtype) == ("results", "outcome")]
    by_day: dict[tuple[str, date], list[ClassifiedFiling]] = defaultdict(list)
    for a in anchors:
        by_day[(a.symbol, a.filed_at.date())].append(a)
    for (sym, day), outs in by_day.items():
        members = [f for f in uniq if f.symbol == sym and f.filed_at.date() == day and f.id not in used
                   and ((f.category, f.subtype) in RESULTS_FOLD or f.category == "dividend")]
        used.update(f.id for f in members)
        ref = _match_results_ref(results_refs.get(sym, []), day)
        parts = sorted({FOLD_PART_LABEL.get((f.category, f.subtype)) or FOLD_PART_LABEL.get((f.category, None)) or ""
                        for f in members} - {""})
        q = _quarter_label(ref.period_key if ref else None)
        title = f"{q} results" if q else "Quarterly results"
        if parts:
            title += " · with " + ", ".join(parts)
        events.append(EventDraft(symbol=sym, company=outs[0].company, category="results", subtype="outcome",
                                 title=title, filings=members, results_ref=ref,
                                 duplicates=sum(dup_count[f.id] for f in members)))

    # 3. management bundles: same symbol + same minute
    mg: dict[tuple[str, datetime], list[ClassifiedFiling]] = defaultdict(list)
    for f in uniq:
        if f.category == "management_change" and f.id not in used:
            mg[(f.symbol, f.filed_at.replace(second=0))].append(f)
    for (sym, _), members in mg.items():
        used.update(f.id for f in members)
        ranked = sorted(members, key=lambda f: MGMT_RANK.index(f.subtype) if f.subtype in MGMT_RANK else 99)
        top = ranked[0]
        people: list[str] = []
        for f in ranked:
            for p in ([f.primary_person] if f.primary_person else []) + f.people:
                norm = p.replace(".", "").replace(" ", "").lower() if p else ""
                if not p or any(norm == q.replace(".", "").replace(" ", "").lower() for q in people):
                    continue
                if " " not in p and any(q.split()[-1].lower() == p.lower() for q in people):
                    continue  # surname-only re-mention
                people.append(p)
        label = MGMT_LABEL.get(top.subtype or "", "Management change")
        who = top.primary_person or (people[0] if people else None)
        title = f"{label}: {who}" if who else (top.title if top.subtype is None else label)
        if len(members) > 1:
            title += f" · +{len(members) - 1} more board change{'s' if len(members) > 2 else ''}"
        events.append(EventDraft(symbol=sym, company=top.company, category="management_change", subtype=top.subtype,
                                 title=title, filings=ranked, people=people[:6],
                                 duplicates=sum(dup_count[f.id] for f in members)))

    # 4. storylines: one deal / case / issue filed in pieces over days (PERSISTENT's QIP:
    #    launch, floor price, allotment, closure). Same symbol + category + subtype within
    #    STORY_DAYS of the previous piece -> one event titled by its latest filing.
    rest = [f for f in uniq if f.id not in used and f.category in STORY_CATS
            and f.subtype not in (None, "uncertain", "debt_routine")]
    groups: dict[tuple[str, str, str | None], list[list[ClassifiedFiling]]] = defaultdict(list)
    for f in rest:
        chains = groups[(f.symbol, f.category, f.subtype)]
        if chains and f.filed_at - chains[-1][-1].filed_at <= timedelta(days=STORY_DAYS):
            chains[-1].append(f)
        else:
            chains.append([f])
    # Untyped pieces of the same symbol + category (e.g. 'Issue of Securities' around a
    # QIP) join the nearest chain if they fall within its window.
    loose = [f for f in uniq if f.id not in used and f.category in STORY_CATS and f.subtype is None]
    for f in loose:
        best = None
        for (sym, cat, _sub), chains in groups.items():
            if sym != f.symbol or cat != f.category:
                continue
            for chain in chains:
                lo = chain[0].filed_at - timedelta(days=STORY_DAYS)
                hi = chain[-1].filed_at + timedelta(days=STORY_DAYS)
                if lo <= f.filed_at <= hi and len(chain) >= 1 and (best is None or len(chain) > len(best)):
                    best = chain
        if best is not None and len(best) >= 2:
            best.append(f)
            best.sort(key=lambda x: x.filed_at)
    for (sym, cat, sub), chains in groups.items():
        for chain in chains:
            if len(chain) < 2:
                continue
            used.update(f.id for f in chain)
            typed = [f for f in chain if f.subtype == sub]
            latest = typed[-1] if typed else chain[-1]
            span = (chain[-1].filed_at.date() - chain[0].filed_at.date()).days
            title = f"{latest.title[:130]} · {len(chain)} filings" + (f" over {span + 1} days" if span else "")
            people = [p for f in chain for p in f.people][:4]
            events.append(EventDraft(symbol=sym, company=latest.company, category=cat, subtype=sub, title=title,
                                     filings=sorted(chain, key=lambda f: f.filed_at, reverse=True), people=people,
                                     duplicates=sum(dup_count[f.id] for f in chain)))

    # 5. everything else, one event per filing
    for f in uniq:
        if f.id in used:
            continue
        events.append(EventDraft(symbol=f.symbol, company=f.company, category=f.category, subtype=f.subtype,
                                 title=f.title[:160], filings=[f], people=f.people[:4], duplicates=dup_count[f.id]))
    return events
