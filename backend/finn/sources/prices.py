"""EOD price/volume via yfinance. One batched download; per-symbol fail-soft."""
from __future__ import annotations

import logging

from ..models import PriceSeries, SourceStatus

log = logging.getLogger("finn.prices")


def yahoo_ticker(symbol: str) -> str:
    return symbol if symbol.startswith("^") else f"{symbol}.NS"


def fetch_prices(symbols: list[str], period: str = "1y") -> tuple[list[PriceSeries], list[SourceStatus]]:
    try:
        import yfinance as yf
        tickers = [yahoo_ticker(s) for s in symbols]
        df = yf.download(tickers, period=period, interval="1d", group_by="ticker",
                         auto_adjust=False, progress=False, threads=True)
    except Exception as e:  # yfinance raises a zoo of exception types
        return [], [SourceStatus(source="yahoo", ok=False, error=f"{type(e).__name__}: {e}")]

    series: list[PriceSeries] = []
    status: list[SourceStatus] = []
    for sym in symbols:
        t = yahoo_ticker(sym)
        try:
            # newer yfinance returns (ticker, field) columns even for a single ticker
            multi = getattr(df.columns, "nlevels", 1) > 1
            sub = df[t] if (len(symbols) > 1 or (multi and t in df.columns.get_level_values(0))) else df
            sub = sub[["Close", "Volume"]].dropna(subset=["Close"])
            if sub.empty:
                raise ValueError("no rows")
            series.append(PriceSeries(
                symbol=sym,
                dates=[d.date() for d in sub.index],
                close=[round(float(c), 2) for c in sub["Close"]],
                volume=[int(v) if v == v else 0 for v in sub["Volume"]],
            ))
            status.append(SourceStatus(source="yahoo", symbol=sym, ok=True,
                                       detail=f"{len(sub)} days to {sub.index[-1].date()}"))
        except Exception as e:
            status.append(SourceStatus(source="yahoo", symbol=sym, ok=False, error=f"{type(e).__name__}: {e}"))
    return series, status
