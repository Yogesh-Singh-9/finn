"""Local data refresh: NSE filings (last N days), results XBRL, calendar, EOD prices,
screener fundamentals -> data/processed/. Run from backend/:

    .venv/Scripts/python scripts/refresh.py              # everything
    .venv/Scripts/python scripts/refresh.py --only nse   # one source group
    .venv/Scripts/python scripts/refresh.py --symbols TRENT,INFY

Fail-soft: a source that fails for a symbol keeps the previous good data for it,
so a flaky refresh never makes the demo worse than the last good run.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finn import config  # noqa: E402
from finn.http import Fetcher  # noqa: E402
from finn.models import Filing, FilingsSnapshot, PricesSnapshot, SourceStatus  # noqa: E402
from finn.sources import nse, prices, screener  # noqa: E402

P = config.PROCESSED_DIR


def write(path: Path, model, indent: int | None = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=indent), encoding="utf-8")


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def refresh_nse(f: Fetcher, symbols: list[str], days: int, status: list[SourceStatus]) -> None:
    nse.warmup(f)
    end = date.today()
    start = end - timedelta(days=days)

    # Carry-over: previous good filings per symbol, used if this run fails for it.
    prev = load_json(P / "filings.json") or {}
    prev_by_sym: dict[str, list[dict]] = {}
    for fl in prev.get("filings", []):
        prev_by_sym.setdefault(fl["symbol"], []).append(fl)

    all_filings: list[Filing] = []
    for sym in symbols:
        fl, st = nse.announcements(f, sym, start, end)
        if not st.ok and sym in prev_by_sym:
            fl = [Filing.model_validate(x) for x in prev_by_sym[sym]]
            st.detail = f"kept {len(fl)} filings from previous run"
        all_filings.extend(fl)
        status.append(st)

        refs, st = nse.results_filings(f, sym)
        status.append(st)
        ref = nse.latest_results_ref(refs)
        out_path = P / "results" / f"{sym}.json"
        if ref is None:
            continue
        xb, st = nse.fetch_xbrl(f, ref)
        status.append(st)
        if xb is None and out_path.exists():
            continue  # keep previous
        payload = {
            "symbol": sym,
            "filings": [r.model_dump(mode="json") for r in refs[:8]],
            "latest": ref.model_dump(mode="json"),
            "latest_xbrl": xb.model_dump(mode="json") if xb else None,
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    # A partial run (--symbols X) must not drop other symbols' filings.
    for sym, rows in prev_by_sym.items():
        if sym not in symbols:
            all_filings.extend(Filing.model_validate(x) for x in rows)
    all_symbols = list(dict.fromkeys(symbols + list(prev.get("symbols", []))))
    all_filings.sort(key=lambda x: x.filed_at, reverse=True)
    write(P / "filings.json", FilingsSnapshot(
        generated_at=datetime.now().replace(microsecond=0), window_from=start, window_to=end,
        symbols=all_symbols, filings=all_filings,
        status=[s for s in status if s.source == "nse.announcements"],
    ))

    events, st = nse.event_calendar(f, set(symbols))
    status.append(st)
    if st.ok:
        (P / "calendar.json").write_text(
            json.dumps([e.model_dump(mode="json") for e in events], indent=1), encoding="utf-8")


def refresh_prices(symbols: list[str], status: list[SourceStatus]) -> None:
    series, st = prices.fetch_prices(symbols + [config.BENCHMARK])
    status.extend(st)
    prev = load_json(P / "prices.json") or {}
    got = {s.symbol for s in series}
    from finn.models import PriceSeries
    for old in prev.get("series", []):
        if old["symbol"] not in got:
            series.append(PriceSeries.model_validate(old))  # keep last good
    if series:
        write(P / "prices.json", PricesSnapshot(
            generated_at=datetime.now().replace(microsecond=0), series=series,
            status=[s for s in st]), indent=None)


def refresh_screener(f: Fetcher, symbols: list[str], status: list[SourceStatus]) -> None:
    for sym in symbols:
        fund, st = screener.fetch_fundamentals(f, sym)
        status.append(st)
        if fund:
            write(P / "fundamentals" / f"{sym}.json", fund)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=config.SNAPSHOT_DAYS)
    ap.add_argument("--symbols", help="comma list; default = portfolio")
    ap.add_argument("--only", choices=["nse", "prices", "screener"])
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    portfolio = args.symbols.split(",") if args.symbols else config.PORTFOLIO
    research = [] if args.symbols else config.RESEARCH_DEMO
    status: list[SourceStatus] = []
    t0 = time.monotonic()

    with Fetcher() as f:
        if args.only in (None, "nse"):
            refresh_nse(f, portfolio, args.days, status)
        if args.only in (None, "prices"):
            refresh_prices(portfolio, status)
        if args.only in (None, "screener"):
            refresh_screener(f, portfolio + research, status)
        redirects = dict(f.redirects)

    manifest = {
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "elapsed_s": round(time.monotonic() - t0, 1),
        "redirects": redirects,
        "failures": [s.model_dump() for s in status if not s.ok],
        "status": [s.model_dump() for s in status],
    }
    P.mkdir(parents=True, exist_ok=True)
    (P / f"manifest{'_' + args.only if args.only else ''}.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8")

    for s in status:
        flag = "ok " if s.ok else "FAIL"
        print(f"{flag} {s.source:18s} {s.symbol or '':11s} {s.detail or s.error or ''}")
    print(f"\n{len(status) - len(manifest['failures'])}/{len(status)} ok in {manifest['elapsed_s']}s;"
          f" redirects: {redirects or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
