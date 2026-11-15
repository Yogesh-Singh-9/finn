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
from ..models import (BriefSnapshot, Event, FilingRef, FilingsSnapshot, LadderStats, Move, PricesSnapshot,
                      ResultsFilingRef)
from .events import fold
from .. import llm
from ..cards import mgmt as mgmt_cards
from ..cards.oneoffs import detect as detect_oneoffs
from ..cards.results import apply_one_offs, build_card, surprise
from ..cards.summaries import summarise
from .score import guidance_part, numbers_part, reaction, score, tier_for, ttm_revenue

log = logging.getLogger("finn.brief")
TIER_ORDER = {"needs_you": 0, "glance": 1, "set_aside": 2}



def attach_guidance(card, fund: dict | None, results_date):
    """Push and pull share one promise store: data/processed/promises/<SYM>.json, written
    by the research build. The card gets last call's guidance vs this quarter."""
    from ..models import Ledger
    from ..research.ledger import card_rows
    path = config.PROCESSED_DIR / "promises" / f"{card.symbol}.json"
    if not path.exists() or not fund:
        card.guidance_note = "no promise ledger built for this company yet"
        return card
    try:
        led = Ledger.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        card.guidance_note = "promise ledger unreadable"
        return card
    adjusted = {card.period_key: {a.key: a.model_dump() for a in card.adjusted}} if card.adjusted else {}
    card.guidance = card_rows(led, fund, card.period_key, results_date, adjusted)
    card.guidance_note = (f"{len(card.guidance)} guided item(s) from calls before these results"
                          if card.guidance else "no gradeable guidance from calls before these results")
    return card

def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def build(use_llm: bool = True) -> BriefSnapshot:
    llm.reset_usage()
    P = config.PROCESSED_DIR
    filings = FilingsSnapshot.model_validate_json((P / "filings.json").read_text(encoding="utf-8"))
    prices_raw = _load(P / "prices.json")
    prices = PricesSnapshot.model_validate(prices_raw) if prices_raw else None
    series = {s.symbol: s for s in prices.series} if prices else {}
    nifty = series.get(config.BENCHMARK)

    refs: dict[str, list[ResultsFilingRef]] = {}
    xbrl: dict[str, dict] = {}
    for sym in filings.symbols:
        r = _load(P / "results" / f"{sym}.json")
        if r:
            refs[sym] = [ResultsFilingRef.model_validate(x) for x in r.get("filings", [])]
            if r.get("latest_xbrl"):
                xbrl[sym] = r["latest_xbrl"]
    fund = {sym: _load(P / "fundamentals" / f"{sym}.json") for sym in filings.symbols}
    revenue = {sym: ttm_revenue(fund[sym]) for sym in filings.symbols}

    with Fetcher() as f:
        classified, cstats = classify(filings.filings, f, use_llm=use_llm)

    drafts = fold(classified, refs)
    cards = {}
    for i, d in enumerate(drafts):
        if (d.category, d.subtype) == ("results", "outcome") and d.results_ref and d.results_ref.period_key:
            outcome = next((x for x in d.filings if (x.category, x.subtype) == ("results", "outcome")), d.filings[0])
            c = build_card(d.symbol, d.results_ref.period_key, fund.get(d.symbol), xbrl.get(d.symbol),
                           filing_url=outcome.attachment_url)
            if c:
                # One-offs: the outcome PDF + same-day press release / deck / letter.
                urls = [x.attachment_url for x in d.filings if x.attachment_url and x.category in
                        ("results", "investor_meetings")]
                xq = (xbrl.get(d.symbol) or {})
                with Fetcher() as f:
                    o, adj, by = detect_oneoffs(
                        urls, xq.get("quarter") if xq.get("period_key") == c.period_key else None,
                        {m.key: m.value for m in c.metrics}, {m.key: m.prev_y for m in c.metrics}, f,
                        use_llm=use_llm)
                c = apply_one_offs(c, o, adj, by)
                c = attach_guidance(c, fund.get(d.symbol), d.occurred_at.date())
            cards[i] = c
    scored = []
    for i, d in enumerate(drafts):
        extra = None
        if (d.category, d.subtype) == ("results", "outcome"):
            c = cards.get(i)
            extra = [numbers_part(c, surprise(c, fund.get(d.symbol)) if c else []), guidance_part(c)]
        scored.append((d, *score(d, revenue.get(d.symbol), series.get(d.symbol), nifty, results_parts=extra)))

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
    for i, (d, total, parts, react, pending) in enumerate(scored):
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
            results_card=cards.get(i),
        ))
    # Cards and summaries don't change the score; they're what the feed shows.
    with Fetcher() as f:
        for e in events:
            if e.category == "management_change":
                e.mgmt_card = mgmt_cards.build_card(e.symbol, e.company, [(x.url, x.subtype) for x in e.filings],
                                                    e.people, f, use_llm=use_llm)
                if e.mgmt_card:
                    e.mgmt_card.short = mgmt_cards.short_headline(e.mgmt_card)
                    e.summary, e.summary_by = e.mgmt_card.headline, f"mgmt card ({e.mgmt_card.extracted_by})"
        sstats = summarise(events, f, use_llm=use_llm)

    # Thesis: which driver of the holding's thesis each event touches (supports/challenges).
    # The thesis part is a fixed table; the score and tier are recomputed from the parts.
    from ..holdings.thesis import load_theses, tag as tag_thesis
    tstats = tag_thesis(events, load_theses(), use_llm=use_llm)
    for e in events:
        total = sum(p.points for p in e.breakdown)
        e.score = round(max(0.0, min(100.0, total)), 1)
        e.tier = tier_for(e.score)  # type: ignore[assignment]

    # Displayed score caps at 100; ranking uses the uncapped sum so two capped events
    # still order by how much evidence each has.
    events.sort(key=lambda e: (TIER_ORDER[e.tier], -sum(p.points for p in e.breakdown), -e.occurred_at.timestamp()))

    # Unusual moves in the last 5 sessions, whether or not a filing explains them.
    owners = {(e.symbol, e.reaction.session): e.id for e in events if e.reaction and
              any(p.part == "reaction" and p.points for p in e.breakdown)}
    moves: list[Move] = []
    for sym in filings.symbols:
        ps = series.get(sym)
        if not ps or len(ps.close) < 70:
            continue
        for i in range(len(ps.close) - 5, len(ps.close)):
            r, _ = reaction(ps, nifty, datetime.combine(ps.dates[i], datetime.min.time()))
            if not r or r.session != ps.dates[i]:
                continue
            if (r.z is not None and abs(r.z) >= 2) or (r.vol_x or 0) >= 2.5:
                moves.append(Move(symbol=sym, session=r.session, close=round(ps.close[i], 2), ret=r.ret,
                                  abn_ret=r.abn_ret, z=r.z, vol_x=r.vol_x, event_id=owners.get((sym, r.session)),
                                  spark=[round(c, 2) for c in ps.close[max(0, i - 29):i + 1]]))
    moves.sort(key=lambda m: (m.session, abs(m.z or 0)), reverse=True)

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
        cards={"results": sum(1 for e in events if e.results_card),
               "results_unverified": sum(1 for e in events if e.results_card and not e.results_card.verified),
               "results_one_offs": sum(1 for e in events if e.results_card and e.results_card.one_offs),
               "mgmt": sum(1 for e in events if e.mgmt_card),
               "mgmt_llm": sum(1 for e in events if e.mgmt_card and e.mgmt_card.extracted_by.startswith(("gemini", "groq"))),
               **{f"summaries_{k}": v for k, v in sstats.items()},
               **{f"thesis_{k}": v for k, v in tstats.items()}},
        llm_usage=llm.USAGE.line(), moves=moves,
    )


def write(snapshot: BriefSnapshot) -> Path:
    out = config.SNAPSHOT_DIR / "brief.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # UTF-8 on disk, and ASCII-escaped (₹ -> ₹) so the file loads even in a reader
    # that ignores the encoding (Windows' cp1252 default).
    out.write_text(json.dumps(snapshot.model_dump(mode="json"), indent=1, ensure_ascii=True), encoding="utf-8")
    return out
