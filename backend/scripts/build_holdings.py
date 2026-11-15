"""Holdings snapshot: price rows (1y closes/volumes for sparklines and the alert
estimator), a seeded thesis per holding, and a 'watch next quarter' line.

    .venv/Scripts/python scripts/build_holdings.py            # all holdings
    .venv/Scripts/python scripts/build_holdings.py --symbols TITAN,INFY

Order of a full rebuild: build_research.py -> build_holdings.py -> build_brief.py
(the brief tags events against the theses written here)."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from finn import config, llm  # noqa: E402
from finn.holdings import thesis as TH  # noqa: E402
from finn.http import Fetcher  # noqa: E402
from finn.models import HoldingRow, HoldingsSnapshot  # noqa: E402

SESSIONS = 250


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    P = config.PROCESSED_DIR
    only = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    prices = {s["symbol"]: s for s in json.loads((P / "prices.json").read_text(encoding="utf-8"))["series"]}
    theses = {k: v.model_dump(mode="json") for k, v in TH.load_theses().items()}
    research = set()
    idx = config.SNAPSHOT_DIR / "research" / "index.json"
    if idx.exists():
        research = {x["symbol"] for x in json.loads(idx.read_text(encoding="utf-8"))}
    rows = []
    with Fetcher() as f:
        for sym in config.PORTFOLIO:
            fp = P / "fundamentals" / f"{sym}.json"
            fund = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else None
            if not only or sym in only or sym not in theses:
                t = TH.seed(sym, fund, f, use_llm=not a.no_llm)
                if t:
                    theses[sym] = t.model_dump(mode="json")
                    print(f"{sym}: {t.line} | {t.watch or 'no watch line'}" + (f" | notes: {t.notes}" if t.notes else ""))
            ps = prices.get(sym) or {}
            c, v, d = ps.get("close", [])[-SESSIONS:], ps.get("volume", [])[-SESSIONS:], ps.get("dates", [])[-SESSIONS:]
            rows.append(HoldingRow(
                symbol=sym, company=(fund or {}).get("company", sym), close=c[-1] if c else None,
                day_change=(c[-1] / c[-2] - 1) if len(c) > 1 and c[-2] else None,
                dates=[date.fromisoformat(x[:10]) for x in d], closes=[round(x, 2) for x in c], volumes=[float(x or 0) for x in v],
                thesis=TH.Thesis.model_validate(theses[sym]) if sym in theses else None, research=sym in research))
    TH.STORE.write_text(json.dumps(theses, ensure_ascii=True), encoding="utf-8")
    counts: Counter[str] = Counter()
    bp = config.SNAPSHOT_DIR / "brief.json"
    if bp.exists():
        for e in json.loads(bp.read_text(encoding="utf-8"))["events"]:
            counts[e["category"]] += 1
    snap = HoldingsSnapshot(generated_at=datetime.now().replace(microsecond=0),
                            prices_as_of=max((r.dates[-1] for r in rows if r.dates), default=None),
                            rows=rows, category_counts_30d=dict(counts), llm_usage=llm.USAGE.line())
    (config.SNAPSHOT_DIR / "holdings.json").write_text(json.dumps(snap.model_dump(mode="json"), ensure_ascii=True), encoding="utf-8")
    print(f"holdings.json: {len(rows)} rows · {llm.USAGE.line()}")


if __name__ == "__main__":
    main()
