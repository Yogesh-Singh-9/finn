"""Dossier numbers from screener's annual tables. All arithmetic here, none in a model.
Each section carries a one-line conclusion (shown when the section is closed) built in
code from the numbers it shows."""
from __future__ import annotations

from typing import Any

from ..models import Cite, Section, Vital

# "Something's off" thresholds for the vital-sign chips (shown in each chip's note).
GROWTH_MIN = 0.08        # 3y revenue CAGR
MARGIN_DROP_BPS = 200    # latest FY EBITDA margin vs 5y average
CASH_MIN = 0.70          # 5y CFO / PAT
ROE_MIN = 0.12
DE_MAX = 1.0


class _T:
    def __init__(self, table: dict | None):
        t = table or {}
        self.cols: list[str] = [c for c in t.get("columns", [])]
        self.labels: list[str] = t.get("column_labels", [])
        self.rows: dict[str, list[Any]] = t.get("rows", {})

    def fy_cols(self, march_only: bool = True) -> list[str]:
        out = []
        for i, c in enumerate(self.cols):
            if not c.startswith("FY"):
                continue
            if march_only and i < len(self.labels) and self.labels[i] and not self.labels[i].startswith("Mar"):
                continue  # balance sheet's half-year column ("Sep 2026") isn't a year-end
            out.append(c)
        return out

    def get(self, row: str, col: str) -> float | None:
        r = self.rows.get(row)
        if not r or col not in self.cols:
            return None
        v = r[self.cols.index(col)]
        return float(v) if isinstance(v, (int, float)) else None

    def find(self, prefix: str) -> str | None:
        return next((r for r in self.rows if r.startswith(prefix)), None)


def _cagr(a: float | None, b: float | None, years: int) -> float | None:
    if a is None or b is None or a <= 0 or b <= 0 or years <= 0:
        return None
    return (b / a) ** (1 / years) - 1


def _pct(x: float | None, digits: int = 0, sign: bool = False) -> str:
    if x is None:
        return "n/a"
    s = f"{x * 100:+.{digits}f}%" if sign else f"{x * 100:.{digits}f}%"
    return s.replace("-", "−")


def _cr(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"₹{x:,.0f} cr".replace("-", "−")


def build(fund: dict, price: dict | None) -> tuple[list[Vital], list[Section], dict[str, Any]]:
    """(vitals, sections, facts). `facts` is the flat dict of computed numbers the
    verdict and the bull/bear prompt are allowed to quote."""
    url = fund.get("source_url") or ""
    pl, bs, cf, rt = _T(fund.get("profit_loss")), _T(fund.get("balance_sheet")), _T(fund.get("cash_flow")), _T(fund.get("ratios"))
    years = pl.fy_cols(march_only=False)
    bank = fund.get("layout") == "bank"
    last = years[-1] if years else None
    scr = lambda what: Cite(label=f"screener {what}", doc_id=url)  # noqa: E731
    facts: dict[str, Any] = {"latest_fy": last}

    # ---- trajectory
    traj = []
    for y in years[-10:]:
        s, op, np_ = pl.get("Sales", y), pl.get("Operating Profit", y), pl.get("Net Profit", y)
        traj.append({"fy": y, "revenue": s, "ebitda": op, "pat": np_,
                     "margin": (op / s) if (s and op is not None and not bank) else None})
    def cagr_n(row: str, n: int) -> float | None:
        if len(years) <= n:
            return None
        return _cagr(pl.get(row, years[-1 - n]), pl.get(row, years[-1]), n)
    rev3, rev5, rev10 = cagr_n("Sales", 3), cagr_n("Sales", 5), cagr_n("Sales", min(10, len(years) - 1))
    pat3, pat5 = cagr_n("Net Profit", 3), cagr_n("Net Profit", 5)
    margins = [t["margin"] for t in traj if t["margin"] is not None]
    m_last = margins[-1] if margins else None
    m_avg5 = sum(margins[-6:-1]) / len(margins[-6:-1]) if len(margins) >= 3 else None
    m_delta = (m_last - m_avg5) * 1e4 if m_last is not None and m_avg5 is not None else None
    facts.update(rev_cagr_3y=rev3, rev_cagr_5y=rev5, rev_cagr_10y=rev10, pat_cagr_3y=pat3, pat_cagr_5y=pat5,
                 ebitda_margin_latest=m_last, ebitda_margin_5y_avg=m_avg5, margin_vs_avg_bps=m_delta)
    traj_concl = f"Revenue {_pct(rev5)} a year over 5 years, PAT {_pct(pat5)}"
    if m_last is not None and m_avg5 is not None:
        traj_concl += f"; EBITDA margin {_pct(m_last, 1)} in {last} vs {_pct(m_avg5, 1)} 5-year average"
    trajectory = Section(key="trajectory", title="Trajectory", conclusion=traj_concl, series=traj,
                         rows=[{"label": "Revenue CAGR", "y3": rev3, "y5": rev5, "y10": rev10},
                               {"label": "PAT CAGR", "y3": pat3, "y5": pat5, "y10": cagr_n("Net Profit", min(10, len(years) - 1))}],
                         notes=["EBITDA = screener's Operating Profit (folds associate/JV share into costs)."])

    # ---- balance sheet
    bsy = bs.fy_cols()
    de_series, roe_series = [], []
    for i, y in enumerate(bsy):
        eq = (bs.get("Equity Capital", y) or 0) + (bs.get("Reserves", y) or 0)
        b = bs.get("Borrowings", y)
        de_series.append({"fy": y, "de": (b / eq) if (eq > 0 and b is not None) else None, "borrowings": b, "equity": eq})
        np_ = pl.get("Net Profit", y)
        if i > 0 and np_ is not None:
            prev = (bs.get("Equity Capital", bsy[i - 1]) or 0) + (bs.get("Reserves", bsy[i - 1]) or 0)
            avg = (eq + prev) / 2
            roe_series.append({"fy": y, "roe": np_ / avg if avg > 0 else None})
    de = de_series[-1]["de"] if de_series else None
    wc = [{"fy": y, "debtor": rt.get("Debtor Days", y), "inventory": rt.get("Inventory Days", y),
           "payable": rt.get("Days Payable", y), "ccc": rt.get("Cash Conversion Cycle", y), "wc_days": rt.get("Working Capital Days", y)}
          for y in rt.fy_cols(march_only=False)[-6:]]
    wc_last = wc[-1] if wc else {}
    wc_first = wc[0] if wc else {}
    facts.update(de=de, borrowings=de_series[-1]["borrowings"] if de_series else None,
                 wc_days=wc_last.get("wc_days"), ccc=wc_last.get("ccc"), inventory_days=wc_last.get("inventory"),
                 ccc_5y_ago=wc_first.get("ccc"))
    bs_concl = "Bank balance sheet: D/E not meaningful" if bank else (
        f"D/E {de:.2f} ({_cr(facts['borrowings'])} borrowings)" if de is not None else "D/E n/a")
    if wc_last.get("ccc") is not None and not bank:
        bs_concl += f"; cash conversion cycle {wc_last['ccc']:.0f} days"
        if wc_first.get("ccc") is not None:
            bs_concl += f" vs {wc_first['ccc']:.0f} in {wc_first['fy']}"
    balance = Section(key="balance", title="Balance sheet", conclusion=bs_concl, series=de_series[-6:], rows=wc,
                      notes=["D/E = Borrowings / (Equity Capital + Reserves), year-end (March) columns. "
                             "Borrowings can include working-capital lines (e.g. gold metal loans); screener doesn't split them."])

    # ---- cash quality
    cfo_row, fcf_row = "Cash from Operating Activity", "Free Cash Flow"
    cfy = cf.fy_cols(march_only=False)
    cash = []
    for y in cfy[-6:]:
        cfo, fcf, np_ = cf.get(cfo_row, y), cf.get(fcf_row, y), pl.get("Net Profit", y)
        cash.append({"fy": y, "cfo": cfo, "fcf": fcf, "pat": np_, "cfo_pat": (cfo / np_) if (cfo is not None and np_ and np_ > 0) else None})
    last5 = cash[-5:]
    s_cfo = sum(c["cfo"] for c in last5 if c["cfo"] is not None)
    s_pat = sum(c["pat"] for c in last5 if c["pat"] is not None)
    s_fcf = sum(c["fcf"] for c in last5 if c["fcf"] is not None)
    cfo_pat5 = s_cfo / s_pat if s_pat > 0 and last5 else None
    neg_fcf_years = [c["fy"] for c in last5 if c["fcf"] is not None and c["fcf"] < 0]
    facts.update(cfo_pat_5y=cfo_pat5, fcf_5y=s_fcf if last5 else None, negative_fcf_years=neg_fcf_years)
    cash_concl = (f"{cfo_pat5:.2f}× of 5-year profit turned into operating cash; {_cr(s_fcf)} free cash flow over 5 years"
                  if cfo_pat5 is not None else "Cash-flow history too short")
    if neg_fcf_years:
        cash_concl += f" (negative in {', '.join(neg_fcf_years)})"
    cashq = Section(key="cash", title="Cash quality", conclusion=cash_concl, series=cash,
                    notes=["CFO/PAT and FCF from screener's cash-flow table; FCF = CFO − capex."])

    # ---- returns
    roe = roe_series[-1]["roe"] if roe_series else None
    roce = rt.get("ROCE %", rt.fy_cols(march_only=False)[-1]) if rt.fy_cols(march_only=False) else None
    facts.update(roe=roe, roce=roce)
    returns = Section(key="returns", title="Returns", conclusion=f"ROE {_pct(roe)} in {last}" + (f", ROCE {_pct(roce)}" if roce is not None else ""),
                      series=[{**r, "roce": rt.get("ROCE %", r["fy"])} for r in roe_series[-6:]],
                      notes=["ROE = Net Profit / average (Equity Capital + Reserves). ROCE as screener reports it."])

    # ---- snapshot / valuation
    eps_ttm = pl.get("EPS in Rs", "TTM")
    np_ttm = pl.get("Net Profit", "TTM")
    px = price.get("close") if price else None
    pe = px / eps_ttm if (px and eps_ttm and eps_ttm > 0) else None
    mcap = px * np_ttm / eps_ttm if (px and np_ttm and eps_ttm) else None
    facts.update(price=px, pe_ttm=pe, mcap_cr=mcap, sales_ttm=pl.get("Sales", "TTM"), pat_ttm=np_ttm)
    snapshot = Section(key="snapshot", title="Snapshot", conclusion=(
        f"₹{mcap:,.0f} cr market cap, {pe:.0f}× trailing earnings" if (mcap and pe) else "Price unavailable"),
        rows=[{"label": "Market cap (approx.)", "value": mcap, "unit": "cr"}, {"label": "P/E (TTM)", "value": pe, "unit": "x"},
              {"label": "Revenue TTM", "value": pl.get("Sales", "TTM"), "unit": "cr"}, {"label": "PAT TTM", "value": np_ttm, "unit": "cr"}],
        notes=["Market cap ≈ price × (TTM PAT / TTM EPS); P/E = price / TTM EPS. Price: EOD close (yfinance)."])

    # ---- vitals (coloured only when off)
    vitals = [
        Vital(key="growth", label="Growth", value=f"{_pct(rev3)} 3y", raw=rev3, off=rev3 is not None and rev3 < GROWTH_MIN,
              note=f"Revenue CAGR over 3 years (screener annual). Flagged below {GROWTH_MIN:.0%}.", source=scr("P&L")),
        Vital(key="margin", label="Margin", value=f"{_pct(m_last, 1)}" + (f" {'+' if m_delta >= 0 else '−'}{abs(m_delta):.0f}bp" if m_delta is not None else ""),
              raw=m_last, off=m_delta is not None and m_delta < -MARGIN_DROP_BPS,
              note=f"EBITDA margin {last} vs its 5-year average. Flagged when more than {MARGIN_DROP_BPS} bps below.", source=scr("P&L")),
        Vital(key="cash", label="Profit→cash", value=f"{cfo_pat5:.2f}×" if cfo_pat5 is not None else "n/a", raw=cfo_pat5,
              off=cfo_pat5 is not None and cfo_pat5 < CASH_MIN,
              note=f"5-year operating cash flow / 5-year PAT. Flagged below {CASH_MIN:.2f}×.", source=scr("cash flow")),
        Vital(key="roe", label="ROE", value=_pct(roe), raw=roe, off=roe is not None and roe < ROE_MIN,
              note=f"PAT / average shareholders' equity, {last}. Flagged below {ROE_MIN:.0%}.", source=scr("balance sheet")),
    ]
    if not bank:
        vitals.append(Vital(key="de", label="D/E", value=f"{de:.2f}" if de is not None else "n/a", raw=de,
                            off=de is not None and de > DE_MAX,
                            note=f"Borrowings / equity at the last March year-end. Flagged above {DE_MAX:.1f}. Borrowings may include working-capital lines.",
                            source=scr("balance sheet")))
    vitals.append(Vital(key="pe", label="P/E", value=f"{pe:.0f}×" if pe else "n/a", raw=pe, off=False,
                        note="Price / TTM EPS. Not judged: context for the rest.", source=scr("P&L TTM")))
    return vitals, [snapshot, trajectory, balance, cashq, returns], facts
