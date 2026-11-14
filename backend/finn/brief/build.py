"""processed/ -> classify -> fold -> score -> snapshots/brief.json. Offline except for
filing PDFs (disk-cached) and, for leftovers only, flash-lite."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from .. import config
from ..classify.classifier import classify
from ..http import Fetcher
from ..models import (BriefSnapshot, Event, FilingRef, FilingsSnapshot, LadderStats, PricesSnapshot,
                      ResultsFilingRef)
from .events import fold
from .score import score, tier_for, ttm_revenue

log = logging.getLogger("finn.brief")
TIER_ORDER = {"needs_you": 0, "glance": 1, "set_aside": 2}


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def build(use_llm: bool = True) -> BriefSnapshot:
    P = config.PROCESSED_DIR
    filings = FilingsSnapshot.model_validate_json((P / "filings.json").read_text(encoding="utf-8"))
    prices_raw = _load(P / "prices.json")
    prices = PricesSnapshot.model_validate(prices_raw) if prices_raw else None
    series = {s.symbol: s for s in prices.series} if prices else {}
    nifty = series.get(config.BENCHMARK)

    refs: dict[str, list[ResultsFilingRef]] = {}
    for sym in filings.symbols:
        r = _load(P / "results" / f"{sym}.json")
        if r:
            refs[sym] = [ResultsFilingRef.model_validate(x) for x in r.get("filings", [])]
    revenue = {sym: ttm_revenue(_load(P / "fundamentals" / f"{sym}.json")) for sym in filings.symbols}

    with Fetcher() as f:
        classified, cstats = classify(filings.filings, f, use_llm=use_llm)

    drafts = fold(classified, refs)
    scored = [(d, *score(d, revenue.get(d.symbol), series.get(d.symbol), nifty)) for d in drafts]

    # One price move, one owner: when several events of a symbol share a reaction
    # session, only the most material one (category + subtype points) is credited.
    by_session: dict[tuple, list[int]] = {}
    for i, (d, _t, _p, react, _pend) in enumerate(scored):
        if react:
            by_session.setdefault((d.symbol, react.session), []).append(i)
    for idxs in by_session.values():
        if len(idxs) < 2:
            continue
        def materiality(i: int) -> tuple[bool, float]:
            # Results own a results-day move; otherwise the most material event does.
            return (scored[i][0].category == "results" and scored[i][0].subtype == "outcome",
                    sum(p.points for p in scored[i][2] if p.part in ("category", "subtype", "uncertain", "amount")))
        owner = max(idxs, key=materiality)
        for i in idxs:
            if i == owner:
                continue
            d, total, parts, react, pending = scored[i]
            for k, p in enumerate(parts):
                if p.part == "reaction" and p.points:
                    parts[k] = p.model_copy(update={
                        "points": 0, "note": p.note + f"; same session as '{scored[owner][0].title[:60]}', credited there"})
            scored[i] = (d, max(0.0, min(100.0, sum(p.points for p in parts))), parts, react, pending)

    events: list[Event] = []
    for d, total, parts, react, pending in scored:
        amount, _ = d.amount
        events.append(Event(
            id=f"{d.symbol}-{d.occurred_at:%Y%m%d%H%M}-{d.filings[0].id}",
            symbol=d.symbol, company=d.company, category=d.category, subtype=d.subtype, title=d.title,
            occurred_at=d.occurred_at,
            filings=[FilingRef(id=x.id, nse_subject=x.nse_subject, title=x.title, category=x.category,
                               subtype=x.subtype, classified_by=x.classified_by, filed_at=x.filed_at,
                               url=x.attachment_url) for x in d.filings],
            people=d.people, amount_cr=amount, reaction=react, reaction_pending=pending,
            results_ref=d.results_ref, score=round(total, 1), breakdown=parts,
            tier=tier_for(total),  # type: ignore[arg-type]
        ))
    events.sort(key=lambda e: (TIER_ORDER[e.tier], -e.score, -e.occurred_at.timestamp()))

    n = len(classified)
    untouched = sum(1 for c in classified if c.classified_by != "llm")
    tiers = {t: sum(1 for e in events if e.tier == t) for t in TIER_ORDER}
    return BriefSnapshot(
        generated_at=datetime.now().replace(microsecond=0),
        window_from=filings.window_from, window_to=filings.window_to,
        prices_as_of=nifty.dates[-1] if nifty else None,
        symbols=filings.symbols, events=events, tiers=tiers,
        ladder=LadderStats(filings=n, by_step=cstats.by_step, pdfs_read=cstats.pdfs_read,
                           llm_calls=cstats.llm_batches, llm_filings=cstats.llm_filings,
                           never_touched_model=untouched,
                           never_touched_model_pct=round(untouched / n, 4) if n else 1.0,
                           llm_status=cstats.llm_status),
    )


def write(snapshot: BriefSnapshot) -> Path:
    out = config.SNAPSHOT_DIR / "brief.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(snapshot.model_dump_json(indent=1), encoding="utf-8")
    return out
