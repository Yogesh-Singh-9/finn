"""Explainable importance score. No model produces an importance number: every point
comes from a visible part, and each event carries its breakdown.

    category base + subtype adjustment + uncertainty floor
    + rupee amount vs TTM revenue + (results) how unusual the numbers are
    + (results, phase 6) miss vs guidance + next-session price/volume reaction + thesis hook

Tiers: needs_you >= 60, glance >= 20, else set_aside.
"""
from __future__ import annotations

import statistics
from bisect import bisect_right
from datetime import date, datetime, time

from ..models import PriceSeries, Reaction, ResultsCard, ScorePart
from ..cards.results import _pct
from .events import EventDraft

NEEDS_YOU, GLANCE = 60, 20
MARKET_CLOSE = time(15, 30)

CATEGORY_BASE: dict[str, int] = {
    "results": 60, "management_change": 30, "M&A": 30, "fund_raise": 30, "litigation": 30,
    "pledging": 30, "credit_rating": 25, "capex": 25, "insider_trading": 20, "bonus_split": 20,
    "press_release": 12, "dividend": 10, "earnings_call": 8, "agm_egm": 5, "postal_ballot": 5,
    "investor_meetings": 3, "unclassified": 0,
}
SUBTYPE_ADJ: dict[tuple[str, str], int] = {
    # results family: only the outcome is news; the paperwork around it isn't
    ("results", "press_release"): -20, ("results", "shareholder_letter"): -20,
    ("results", "newspaper_ad"): -58, ("results", "auditor_report"): -55, ("results", "results_date"): -45,
    # management: who and which direction
    ("management_change", "chairman_exit"): 35, ("management_change", "ceo_md_exit"): 35,
    ("management_change", "cfo_exit"): 30, ("management_change", "vice_chairman_exit"): 20,
    ("management_change", "ceo_md_appoint"): 20, ("management_change", "cfo_appoint"): 15,
    ("management_change", "chairman_appoint"): 15, ("management_change", "executive_director_exit"): 15,
    ("management_change", "executive_director_appoint"): 5, ("management_change", "independent_director_exit"): 5,
    ("management_change", "nonexec_director_exit"): -5, ("management_change", "director_appoint"): -5,
    ("management_change", "senior_mgmt_appoint"): -8, ("management_change", "nonexec_director_appoint"): -10,
    ("management_change", "independent_director_appoint"): -12, ("management_change", "reappointment"): -18,
    # credit: direction matters far more than the event
    ("credit_rating", "downgrade"): 30, ("credit_rating", "outlook_negative"): 15, ("credit_rating", "upgrade"): 10,
    ("credit_rating", "outlook_positive"): 5, ("credit_rating", "assigned"): -10, ("credit_rating", "withdrawal"): -15,
    ("credit_rating", "reaffirm"): -18,
    ("M&A", "subsidiary_ipo"): 15, ("M&A", "acquisition"): 5, ("M&A", "divestment"): 5,
    ("M&A", "stake_change"): -5, ("M&A", "wos_incorporation"): -25,
    ("fund_raise", "qip"): 10, ("fund_raise", "preferential"): 10, ("fund_raise", "rights"): 10,
    ("fund_raise", "buyback"): 15, ("fund_raise", "debt_routine"): -25,
    ("litigation", "regulatory"): 10,
    ("press_release", "clarification"): 8, ("press_release", "approval"): 5, ("press_release", "order_win"): 3,
    ("press_release", "partnership"): -4, ("press_release", "store_opening"): -8, ("press_release", "award"): -12,
    ("earnings_call", "transcript"): 4, ("earnings_call", "call_notice"): -4, ("earnings_call", "recording"): -6,
    ("investor_meetings", "deck"): 2, ("investor_meetings", "meeting"): -3,
}
AMOUNT_CATEGORIES = {"M&A", "fund_raise", "capex", "press_release", "litigation"}


def _amount_part(ev: EventDraft, ttm_revenue: float | None) -> ScorePart | None:
    amount, note = ev.amount
    if ev.category not in AMOUNT_CATEGORIES or ev.subtype == "debt_routine" or amount is None:
        return None
    if not ttm_revenue:
        return ScorePart(part="amount", points=0, note=f"₹{amount:,.0f} cr; revenue unavailable to size it")
    ratio = amount / ttm_revenue
    pts = 25 if ratio >= 0.10 else 15 if ratio >= 0.03 else 8 if ratio >= 0.01 else 3 if ratio >= 0.0025 else 0
    extra = f" ({note})" if note else ""
    return ScorePart(part="amount", points=pts,
                     note=f"₹{amount:,.0f} cr = {ratio:.1%} of TTM revenue ₹{ttm_revenue:,.0f} cr{extra}")


def numbers_part(card: ResultsCard | None, surprises: list[tuple[str, float, float, float, float]]) -> ScorePart:
    """How far this quarter's YoY revenue/PAT growth sits from the company's own last 8
    quarters, in standard deviations. A results day is news because of the numbers, not
    only because the stock moved: PAT -30% on a flat stock must still surface."""
    if card is None:
        return ScorePart(part="numbers", points=0, note="no results card (numbers not available)")
    if not surprises:
        return ScorePart(part="numbers", points=0, note=f"{card.headline}; too little history to judge")
    ranked = sorted(surprises, key=lambda t: -abs(t[2]))
    label, yoy, z, mean, _sd = ranked[0]
    az = abs(z)
    pts = 25 if az >= 3 else 18 if az >= 2 else 10 if az >= 1.5 else 5 if az >= 1 else 0
    # Name the metric that set the points, then the others, so an adjusted PAT
    # (company figure) is always visible even when revenue was the bigger surprise.
    note = f"{label} {_pct(yoy)} YoY vs its usual {_pct(mean)} over the last 8 quarters ({z:+.1f}σ, sets the points)"
    for l2, y2, z2, m2, _ in ranked[1:]:
        note += f"; {l2} {_pct(y2)} vs usual {_pct(m2)} ({z2:+.1f}σ)"
    pat = next((m for m in card.metrics if m.key == "pat"), None)
    if pat and pat.value is not None and pat.prev_y is not None and pat.prev_y > 0 > pat.value:
        pts, note = 25, note + "; swung to a loss"
    elif pat and pat.yoy is not None and pat.yoy <= -0.20 and pts < 18:
        pts, note = 18, note + f"; PAT {_pct(pat.yoy)} YoY"
    if not card.verified:
        note += "; card unverified, open filing"
    return ScorePart(part="numbers", points=pts, note=note)


def guidance_part(card: ResultsCard | None) -> ScorePart:
    """A miss against what management guided on the last call is news on its own; so is a
    full-year target falling behind. Grades come from the promise ledger (Python)."""
    rows = card.guidance if card else []
    if not rows:
        return ScorePart(part="guidance", points=0, note="no gradeable guidance on record from the last call")
    miss = [g for g in rows if g.grade == "missed"]
    behind = [g for g in rows if g.progress_flag == "behind"]
    if miss:
        g = miss[0]
        return ScorePart(part="guidance", points=15, note=f"missed: {g.metric} guided {g.guided} on the {g.call_label}, delivered {g.delivered}")
    if behind:
        g = behind[0]
        return ScorePart(part="guidance", points=8, note=f"behind: {g.metric} guided {g.guided}, {g.delivered}")
    return ScorePart(part="guidance", points=0, note="; ".join(f"{g.metric}: {g.grade.replace('_', ' ')}" + (f" ({g.progress_flag.replace('_', ' ')})" if g.progress_flag else "") for g in rows))


def _session_index(ps: PriceSeries, filed: datetime) -> int | None:
    """Index of the first session that could react: same day if filed during market
    hours on a trading day, else the next session. None if not traded yet."""
    d = filed.date()
    i = bisect_right(ps.dates, d)  # first index with date > d
    if filed.time() <= MARKET_CLOSE and i > 0 and ps.dates[i - 1] == d:
        return i - 1
    return i if i < len(ps.dates) else None


def reaction(ps: PriceSeries | None, nifty: PriceSeries | None, filed: datetime) -> tuple[Reaction | None, bool]:
    """(reaction, pending). Abnormal return = stock - NIFTY; z vs 60-session sigma of
    abnormal returns; volume vs 20-session average. All arithmetic in Python."""
    if ps is None or len(ps.dates) < 25:
        return None, False
    i = _session_index(ps, filed)
    if i is None:
        return None, True
    if i < 21:
        return None, False
    ret = ps.close[i] / ps.close[i - 1] - 1
    n_idx = {d: k for k, d in enumerate(nifty.dates)} if nifty else {}

    def mret(k: int) -> float:
        j = n_idx.get(ps.dates[k])
        return nifty.close[j] / nifty.close[j - 1] - 1 if nifty and j and j > 0 else 0.0

    abn = ret - mret(i)
    hist = [ps.close[k] / ps.close[k - 1] - 1 - mret(k) for k in range(max(1, i - 60), i)]
    sigma = statistics.pstdev(hist) if len(hist) >= 20 else None
    vols = [v for v in ps.volume[max(0, i - 20):i] if v]
    vol_x = ps.volume[i] / (sum(vols) / len(vols)) if vols and ps.volume[i] else None
    return Reaction(session=ps.dates[i], ret=round(ret, 5), abn_ret=round(abn, 5),
                    z=round(abn / sigma, 2) if sigma else None,
                    vol_x=round(vol_x, 2) if vol_x else None), False


def _reaction_part(r: Reaction | None, pending: bool, routine: bool) -> ScorePart:
    if pending:
        return ScorePart(part="reaction", points=0, note="next session not traded yet")
    if r is None:
        return ScorePart(part="reaction", points=0, note="no price data")
    z = abs(r.z) if r.z is not None else 0.0
    vx = r.vol_x or 0.0
    pts = (20 if z >= 3 else 12 if z >= 2 else 6 if z >= 1.5 else 0) + (10 if vx >= 3 else 5 if vx >= 2 else 0)
    desc = (f"{r.session:%d %b}: {r.ret:+.1%} ({r.abn_ret:+.1%} vs NIFTY"
            + (f", {r.z:+.1f}σ" if r.z is not None else "") + ")"
            + (f", volume {r.vol_x:.1f}× 20-day avg" if r.vol_x else ""))
    if routine and pts:
        return ScorePart(part="reaction", points=0, note=desc + "; not credited to routine paperwork")
    return ScorePart(part="reaction", points=pts, note=desc)


def score(ev: EventDraft, ttm_revenue: float | None, ps: PriceSeries | None,
          nifty: PriceSeries | None, results_parts: list[ScorePart] | None = None,
          ) -> tuple[float, list[ScorePart], Reaction | None, bool]:
    parts: list[ScorePart] = []
    base = CATEGORY_BASE.get(ev.category, 0)
    parts.append(ScorePart(part="category", points=base, note=ev.category.replace("_", " ")))
    adj = SUBTYPE_ADJ.get((ev.category, ev.subtype or ""), 0)
    if ev.subtype and ev.subtype != "uncertain":
        parts.append(ScorePart(part="subtype", points=adj, note=ev.subtype.replace("_", " ")))
    if ev.uncertain:
        parts.append(ScorePart(part="uncertain", points=25,
                               note="couldn't classify with confidence; shown rather than hidden"))
    a = _amount_part(ev, ttm_revenue)
    if a:
        parts.append(a)
    if results_parts:
        parts.extend(results_parts)
    r, pending = reaction(ps, nifty, ev.occurred_at)
    parts.append(_reaction_part(r, pending, routine=(base + adj) < 10 and not ev.uncertain))
    parts.append(ScorePart(part="thesis", points=0, note="thesis hook not set yet"))
    total = max(0.0, min(100.0, sum(p.points for p in parts)))
    return total, parts, r, pending


def tier_for(total: float) -> str:
    return "needs_you" if total >= NEEDS_YOU else "glance" if total >= GLANCE else "set_aside"


def ttm_revenue(fund: dict | None) -> float | None:
    """TTM revenue (₹ cr) from screener's P&L ('Sales' or, for banks, 'Revenue')."""
    if not fund:
        return None
    for tbl in ("profit_loss", "quarters"):
        t = fund.get(tbl)
        if not t:
            continue
        row = t["rows"].get("Sales") or t["rows"].get("Revenue")
        if not row:
            continue
        if tbl == "profit_loss" and "TTM" in t["columns"]:
            v = row[t["columns"].index("TTM")]
            if v:
                return v
        if tbl == "quarters":
            last4 = [v for v in row[-4:] if v is not None]
            if len(last4) == 4:
                return sum(last4)
    return None


__all__ = ["score", "tier_for", "ttm_revenue", "reaction", "date"]
