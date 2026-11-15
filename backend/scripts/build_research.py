"""Build research dossiers (and the shared promise store) for one or more tickers.

    .venv/Scripts/python scripts/build_research.py TITAN POLYCAB INFY
    .venv/Scripts/python scripts/build_research.py KPITTECH --refresh   # fetch screener first

Writes data/snapshots/research/<SYM>.json and data/processed/promises/<SYM>.json.
Re-run scripts/build_brief.py afterwards so results cards pick up the guided -> delivered rows."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from finn import config  # noqa: E402
from finn.http import Fetcher  # noqa: E402
from finn.research import dossier  # noqa: E402
from finn.sources.screener import fetch_fundamentals  # noqa: E402


def load_fund(f: Fetcher, sym: str, refresh: bool) -> dict | None:
    path = config.PROCESSED_DIR / "fundamentals" / f"{sym}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    fund, status = fetch_fundamentals(f, sym)
    if fund is None:
        print(f"{sym}: screener unavailable ({status.error})")
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    data = fund.model_dump(mode="json")
    path.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
    return data


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--refresh", action="store_true", help="re-fetch screener for these tickers")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    with Fetcher() as f:
        for sym in [s.upper() for s in a.symbols]:
            fund = load_fund(f, sym, a.refresh)
            if not fund:
                continue
            snap = dossier.build(sym, f, fund, use_llm=not a.no_llm, progress=lambda s: print(f"  {sym}: {s}"))
            dossier.write(snap)
            st = snap.ledger.stats
            print(f"{sym}: {len(snap.ledger.promises)} promises ({st}), bull {len(snap.bull)}, bear {len(snap.bear)}, "
                  f"questions {len(snap.questions)} · {snap.cost_line}")
            for n in snap.notes:
                print(f"  note: {n}")


if __name__ == "__main__":
    main()
