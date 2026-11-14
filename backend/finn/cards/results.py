"""Results card: the quarter's P&L lines with QoQ / YoY, margins, arithmetic checks and a
headline sentence, all computed here. No model touches a number.

One source per number (CLAUDE.md):
- screener's quarterly table when it already has the quarter (every column);
- NSE XBRL for the current column only when screener lags, labelled as such. EBITDA and
  PBT are defined differently in the two sources, so in that case they carry no growth.
Banks/NBFCs: NII = interest earned - interest expended (screener: Revenue - Interest);
PPOP only where the bank XBRL reports it (current quarter, no base); EBITDA is n/a.
"""
from __future__ import annotations

import statistics
from typing import Any

from ..models import Check, Margin, Metric, ResultsCard

QUARTER_BASES = 8  # own-history window for the "how unusual is this" score


def prev_q(key: str) -> str:
    fy, q = int(key[2:4]), int(key[5])
    return f"FY{fy:02d}Q{q - 1}" if q > 1 else f"FY{fy - 1:02d}Q4"


def prev_y(key: str) -> str:
    return f"FY{int(key[2:4]) - 1:02d}Q{key[5]}"


def quarter_label(key: str) -> str:
    return f"{key[4:]} {key[:4]}"


def _growth(cur: float | None, base: float | None) -> float | None:
    if cur is None or base is None or base <= 0:
        return None  # no base, or a loss base: % change is meaningless
    return round(cur / base - 1, 5)


def _bps(cur: float | None, base: float | None) -> float | None:
    return None if cur is None or base is None else round((cur - base) * 1e4)


def _ratio(a: float | None, b: float | None) -> float | None:
    return round(a / b, 5) if a is not None and b else None


class _Q:
    """Screener quarterly table accessor by period key."""

    def __init__(self, fund: dict):
        t = fund.get("quarters") or {}
        self.cols: list[str] = t.get("columns", [])
        self.rows: dict[str, list[Any]] = t.get("rows", {})

    def has(self, key: str) -> bool:
        return key in self.cols

    def get(self, row: str, key: str) -> float | None:
        r = self.rows.get(row)
        if not r or key not in self.cols:
            return None
        return r[self.cols.index(key)]

    def series(self, row: str) -> list[tuple[str, float | None]]:
        return list(zip(self.cols, self.rows.get(row) or [None] * len(self.cols)))


def _metric(key: str, label: str, q: _Q, row: str, cur_key: str, cur: float | None = None,
            source: str = "screener", note: str | None = None, growth: bool = True) -> Metric:
    v = q.get(row, cur_key) if cur is None else cur
    pq, py = q.get(row, prev_q(cur_key)), q.get(row, prev_y(cur_key))
    m = Metric(key=key, label=label, value=v, prev_q=pq, prev_y=py, source=source, note=note)
    if growth:
        m.qoq, m.yoy = _growth(v, pq), _growth(v, py)
        if v is not None and ((pq is not None and pq <= 0) or (py is not None and py <= 0)):
            m.note = (m.note + "; " if m.note else "") + "loss / zero base: no % change"
    return m


def _check(name: str, expected: float | None, actual: float | None, rel: float, floor: float = 2.0) -> Check | None:
    if expected is None or actual is None:
        return None
    tol = max(floor, rel * max(abs(expected), abs(actual)))
    return Check(name=name, expected=round(expected, 2), actual=round(actual, 2), tolerance=round(tol, 2),
                 ok=abs(expected - actual) <= tol)


def _pct(x: float) -> str:
    a = abs(x) * 100
    return f"{'+' if x >= 0 else '−'}{a:.0f}%" if a >= 10 else f"{'+' if x >= 0 else '−'}{a:.1f}%"


def _headline(metrics: dict[str, Metric], margin: Margin | None) -> str:
    bits: list[str] = []
    top = metrics.get("revenue") or metrics.get("nii")
    if top and top.yoy is not None:
        bits.append(f"{'Revenue' if top.key == 'revenue' else 'NII'} {_pct(top.yoy)} YoY")
    if margin and margin.yoy_bps is not None:
        b = margin.yoy_bps
        name = margin.label.split(" (")[0]
        bits.append(f"{name} {'+' if b >= 0 else '−'}{abs(b):.0f} bps")
    pat = metrics.get("pat")
    if pat and pat.yoy is not None:
        bits.append(f"PAT {_pct(pat.yoy)}")
    elif pat and pat.value is not None and pat.prev_y is not None and pat.prev_y <= 0 < pat.value:
        bits.append("PAT back in profit")
    elif pat and pat.value is not None and pat.value < 0:
        bits.append("PAT a loss")
    if not bits:
        return "Numbers filed; no comparable base yet"
    s = ", ".join(bits)
    return s[0].upper() + s[1:]


def _xbrl_tag(x: dict, *names: str) -> float | None:
    for n in names:
        if n in x:
            return x[n]
    return None


def build_card(symbol: str, period_key: str, fund: dict | None, xbrl: dict | None,
               filing_url: str | None = None) -> ResultsCard | None:
    if not fund or not period_key:
        return None
    q = _Q(fund)
    bank = fund.get("layout") == "bank"
    basis = fund.get("basis", "consolidated")
    xq = (xbrl or {}).get("quarter") or {}
    xbrl_ok = bool(xq) and (xbrl or {}).get("period_key") == period_key
    use_xbrl = not q.has(period_key)
    if use_xbrl and not xbrl_ok:
        return None
    src = "NSE XBRL" if use_xbrl else "screener"
    if use_xbrl:
        x_basis = "consolidated" if (xbrl or {}).get("consolidated") else "standalone"
        source_note = (f"screener not updated for {quarter_label(period_key)} yet; current quarter from NSE XBRL "
                       f"({x_basis}), prior quarters from screener ({basis})")
    else:
        source_note = f"screener quarterly table ({basis})"

    metrics: list[Metric] = []
    margins: list[Margin] = []
    checks: list[Check | None] = []
    mixed = "current from NSE XBRL, bases from screener"

    if not bank:
        if use_xbrl:
            rev = _xbrl_tag(xq, "RevenueFromOperations")
            pbt = _xbrl_tag(xq, "ProfitBeforeTax")
            pat = _xbrl_tag(xq, "ProfitLossForPeriod")
            exp, dep, fin = (_xbrl_tag(xq, "Expenses"), _xbrl_tag(xq, "DepreciationDepletionAndAmortisationExpense"),
                             _xbrl_tag(xq, "FinanceCosts"))
            ebitda = rev - (exp - (dep or 0) - (fin or 0)) if rev is not None and exp is not None else None
            metrics += [
                _metric("revenue", "Revenue", q, "Sales", period_key, cur=rev, source=src, note=mixed),
                _metric("ebitda", "EBITDA (XBRL: revenue − operating costs)", q, "Operating Profit", period_key,
                        cur=ebitda, source=src, growth=False,
                        note="defined differently from EBITDA (screener): no QoQ/YoY"),
                _metric("pbt", "PBT (before share of associates/JVs)", q, "Profit before tax", period_key, cur=pbt,
                        source=src, growth=False, note="XBRL PBT excludes associates/JVs, screener's includes them: no QoQ/YoY"),
                _metric("pat", "PAT", q, "Net Profit", period_key, cur=pat, source=src, note=mixed),
            ]
            tax = _xbrl_tag(xq, "TaxExpense")
            jv = _xbrl_tag(xq, "ShareOfProfitLossOfAssociatesAndJointVenturesAccountedForUsingEquityMethod") or 0
            disc = _xbrl_tag(xq, "ProfitLossFromDiscontinuedOperationsAfterTax") or 0
            inc = _xbrl_tag(xq, "Income")
            exc = _xbrl_tag(xq, "ExceptionalItemsBeforeTax") or 0
            checks += [
                _check("income − expenses + exceptional = PBT", (inc - exp + exc) if inc is not None and exp is not None else None, pbt, 0.005),
                _check("PBT − tax + associates/JVs + discontinued = PAT",
                       (pbt - tax + jv + disc) if pbt is not None and tax is not None else None, pat, 0.005),
            ]
            margins.append(Margin(key="ebitda_margin", label="EBITDA margin (XBRL)", value=_ratio(ebitda, rev)))
            margins.append(Margin(key="pat_margin", label="PAT margin", value=_ratio(pat, rev),
                                  prev_q=_ratio(q.get("Net Profit", prev_q(period_key)), q.get("Sales", prev_q(period_key))),
                                  prev_y=_ratio(q.get("Net Profit", prev_y(period_key)), q.get("Sales", prev_y(period_key)))))
        else:
            metrics += [
                _metric("revenue", "Revenue", q, "Sales", period_key),
                _metric("ebitda", "EBITDA (screener)", q, "Operating Profit", period_key,
                        note="screener's Operating Profit; associate/JV share sits in expenses"),
                _metric("pbt", "PBT", q, "Profit before tax", period_key),
                _metric("pat", "PAT", q, "Net Profit", period_key),
            ]
            g = lambda r, k=period_key: q.get(r, k)  # noqa: E731
            sales, op, oi, it, dep, pbt, taxr, pat = (g("Sales"), g("Operating Profit"), g("Other Income"), g("Interest"),
                                                      g("Depreciation"), g("Profit before tax"), g("Tax %"), g("Net Profit"))
            checks += [
                _check("sales − expenses = EBITDA", (sales - g("Expenses")) if sales is not None and g("Expenses") is not None else None, op, 0.01),
                _check("EBITDA + other income − interest − depreciation = PBT",
                       (op + (oi or 0) - (it or 0) - (dep or 0)) if op is not None else None, pbt, 0.015),
                # screener rounds tax % to a whole percent, so allow 0.5pp of PBT plus
                # minority / associate effects of a few percent of PAT
                Check(name="PBT × (1 − tax %) = PAT", expected=round(pbt * (1 - taxr), 2), actual=pat,
                      tolerance=round(max(2.0, 0.006 * abs(pbt) + 0.025 * abs(pat)), 2),
                      ok=abs(pbt * (1 - taxr) - pat) <= max(2.0, 0.006 * abs(pbt) + 0.025 * abs(pat)))
                if None not in (pbt, taxr, pat) else None,
            ]
            for key, label, num in (("ebitda_margin", "EBITDA margin (screener)", "Operating Profit"),
                                    ("pat_margin", "PAT margin", "Net Profit")):
                cur = _ratio(q.get(num, period_key), q.get("Sales", period_key))
                pq_ = _ratio(q.get(num, prev_q(period_key)), q.get("Sales", prev_q(period_key)))
                py_ = _ratio(q.get(num, prev_y(period_key)), q.get("Sales", prev_y(period_key)))
                margins.append(Margin(key=key, label=label, value=cur, prev_q=pq_, prev_y=py_,
                                      qoq_bps=_bps(cur, pq_), yoy_bps=_bps(cur, py_)))
    else:
        def nii(k: str) -> float | None:
            r, i = q.get("Revenue", k), q.get("Interest", k)
            return r - i if r is not None and i is not None else None
        if use_xbrl:
            ie, ix = _xbrl_tag(xq, "InterestEarned"), _xbrl_tag(xq, "InterestExpended")
            cur_nii = ie - ix if ie is not None and ix is not None else None
            pat = _xbrl_tag(xq, "ProfitLossForThePeriod", "ProfitLossForPeriod")
            pbt = _xbrl_tag(xq, "ProfitLossFromOrdinaryActivitiesBeforeTax", "ProfitBeforeTax")
        else:
            cur_nii, pat, pbt = nii(period_key), q.get("Net Profit", period_key), q.get("Profit before tax", period_key)
        m = Metric(key="nii", label="NII (interest earned − interest expended)", value=cur_nii,
                   prev_q=nii(prev_q(period_key)), prev_y=nii(prev_y(period_key)), source=src,
                   note=mixed if use_xbrl else "screener: Revenue − Interest")
        m.qoq, m.yoy = _growth(m.value, m.prev_q), _growth(m.value, m.prev_y)
        metrics.append(m)
        ppop = _xbrl_tag(xq, "OperatingProfitBeforeProvisionAndContingencies") if xbrl_ok else None
        metrics.append(Metric(key="ppop", label="PPOP", value=ppop, source="NSE XBRL" if ppop is not None else None,
                              note=("current quarter from the bank XBRL; screener folds provisions into expenses, "
                                    "so there is no prior-quarter base") if ppop is not None else
                                   "n/a: screener folds provisions into expenses and this filing's XBRL has no PPOP line"))
        metrics.append(Metric(key="ebitda", label="EBITDA", value=None, note="n/a for lenders: interest is the cost of goods"))
        metrics.append(_metric("pbt", "PBT", q, "Profit before tax", period_key, cur=pbt if use_xbrl else None,
                               source=src, growth=not use_xbrl,
                               note="XBRL PBT is after exceptional items: no QoQ/YoY" if use_xbrl else None))
        metrics.append(_metric("pat", "PAT", q, "Net Profit", period_key, cur=pat if use_xbrl else None, source=src,
                               note=mixed if use_xbrl else None))
        if not use_xbrl:
            g = lambda r, k=period_key: q.get(r, k)  # noqa: E731
            fp, oi, dep, taxr = g("Financing Profit"), g("Other Income"), g("Depreciation"), g("Tax %")
            rev, it, ex = g("Revenue"), g("Interest"), g("Expenses")
            checks += [
                _check("revenue − interest − expenses = financing profit",
                       (rev - it - ex) if None not in (rev, it, ex) else None, fp, 0.01),
                _check("financing profit + other income − depreciation = PBT",
                       (fp + (oi or 0) - (dep or 0)) if fp is not None else None, pbt, 0.015),
                Check(name="PBT × (1 − tax %) = PAT", expected=round(pbt * (1 - taxr), 2), actual=pat,
                      tolerance=round(max(2.0, 0.006 * abs(pbt) + 0.025 * abs(pat)), 2),
                      ok=abs(pbt * (1 - taxr) - pat) <= max(2.0, 0.006 * abs(pbt) + 0.025 * abs(pat)))
                if None not in (pbt, taxr, pat) else None,
            ]
            cur = _ratio(pat, rev)
            pq_ = _ratio(q.get("Net Profit", prev_q(period_key)), q.get("Revenue", prev_q(period_key)))
            py_ = _ratio(q.get("Net Profit", prev_y(period_key)), q.get("Revenue", prev_y(period_key)))
            margins.append(Margin(key="pat_margin", label="PAT / revenue", value=cur, prev_q=pq_, prev_y=py_,
                                  qoq_bps=_bps(cur, pq_), yoy_bps=_bps(cur, py_)))

    real_checks = [c for c in checks if c is not None]
    verified = bool(real_checks) and all(c.ok for c in real_checks)
    by_key = {m.key: m for m in metrics}
    lead_margin = next((mg for mg in margins if mg.key == "ebitda_margin" and mg.yoy_bps is not None), None) \
        or next((mg for mg in margins if mg.yoy_bps is not None), None)
    return ResultsCard(
        symbol=symbol, period_key=period_key, period_label=quarter_label(period_key), basis=basis,
        layout="bank" if bank else "standard", source=src, source_note=source_note,
        headline=_headline(by_key, lead_margin), metrics=metrics, margins=margins, checks=real_checks,
        verified=verified, status="verified" if verified else "unverified, open filing",
        filing_url=filing_url, xbrl_url=(xbrl or {}).get("source_url") if use_xbrl or xbrl_ok else None,
    )


def yoy_history(fund: dict | None, row: str, upto_key: str, n: int = QUARTER_BASES) -> list[float]:
    """The company's own YoY growth rates for the n quarters before `upto_key`."""
    if not fund:
        return []
    q = _Q(fund)
    out: list[float] = []
    for k, v in q.series(row):
        if k >= upto_key:  # FYxxQy keys sort chronologically
            continue
        g = _growth(v, q.get(row, prev_y(k)))
        if g is not None:
            out.append(g)
    return out[-n:]


def surprise(card: ResultsCard, fund: dict | None) -> list[tuple[str, float, float, float, float]]:
    """(label, yoy, z, mean, sd) for revenue/NII and PAT vs the company's own last
    QUARTER_BASES YoY growths. sd is floored at 5pp so a steady compounder's 2pp wobble
    doesn't read as a 3σ event."""
    rows = {"revenue": "Sales", "nii": None, "pat": "Net Profit"}
    out = []
    adj = {a.key: a for a in card.adjusted if a.yoy is not None}
    for m in card.metrics:
        if m.key not in rows or m.yoy is None:
            continue
        yoy, label = m.yoy, m.label.split(" (")[0]
        if m.key == "pat" and ("pat" in adj or "pbt" in adj):
            # A one-off isn't the business: score the company's own adjusted figure.
            a = adj.get("pat") or adj["pbt"]
            yoy, label = a.yoy, f"Adjusted {a.key.upper()} (company figure)"
            hist = yoy_history(fund, "Net Profit" if a.key == "pat" else "Profit before tax", card.period_key)
            if len(hist) >= 4:
                mean, sd = statistics.fmean(hist), max(statistics.pstdev(hist), 0.05)
                out.append((label, yoy, (yoy - mean) / sd, mean, sd))
            continue
        if m.key == "nii":
            q = _Q(fund or {})
            hist = []
            for k in q.cols:
                if k >= card.period_key:
                    continue
                def nii(kk: str) -> float | None:
                    r, i = q.get("Revenue", kk), q.get("Interest", kk)
                    return r - i if r is not None and i is not None else None
                g = _growth(nii(k), nii(prev_y(k)))
                if g is not None:
                    hist.append(g)
            hist = hist[-QUARTER_BASES:]
        else:
            hist = yoy_history(fund, rows[m.key], card.period_key)
        if len(hist) < 4:
            continue
        mean = statistics.fmean(hist)
        sd = max(statistics.pstdev(hist), 0.05)
        out.append((label, yoy, (yoy - mean) / sd, mean, sd))
    return out


def apply_one_offs(card: ResultsCard, one_offs: list, adjusted: list, by: str | None) -> ResultsCard:
    """Attach verified one-offs. When the company states an adjusted profit figure, the
    headline leads with it (labelled as the company's own) and keeps the reported number
    beside it; otherwise the headline just says a one-off is inside the reported profit."""
    if not one_offs:
        return card
    card.one_offs, card.adjusted, card.one_offs_by = one_offs, adjusted, by
    card.reported_headline = card.headline
    a = next((x for x in adjusted if x.key == "pat" and x.yoy is not None), None) or \
        next((x for x in adjusted if x.key == "pbt" and x.yoy is not None), None)
    pat = next((m for m in card.metrics if m.key == "pat"), None)
    prof = [o for o in one_offs if o.affects != "revenue"]
    total = sum(o.amount_cr for o in prof)
    kinds = {o.effect for o in prof}
    kind = " gain" if kinds == {"gain"} else " charge" if kinds == {"charge"} else ""
    lead = card.headline.split(", PAT")[0] if ", PAT" in card.headline else card.headline
    if a:
        reported = f"; reported PAT {_pct(pat.yoy)}" if pat and pat.yoy is not None else ""
        card.headline = (f"{lead}, adjusted {a.key.upper()} {_pct(a.yoy)} "
                         f"(company figure, excl. ₹{total:,.0f} cr one-off{kind}{reported})")
    elif total:
        card.headline = f"{card.headline} (incl. ₹{total:,.0f} cr one-off{kind})"
    return card
