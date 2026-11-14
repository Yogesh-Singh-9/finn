"""Build data/snapshots/brief.json from data/processed/. Run from backend/:

    .venv/Scripts/python scripts/build_brief.py            # LLM for leftovers if a key is set
    .venv/Scripts/python scripts/build_brief.py --no-llm
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finn.brief.build import build, write  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # titles carry ₹ and ·; Windows consoles default to cp1252
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--show", type=int, default=40, help="print the top N events")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("pypdf").setLevel(logging.ERROR)

    snap = build(use_llm=not args.no_llm)
    path = write(snap)
    L = snap.ladder
    print(f"{L.filings} filings -> {len(snap.events)} events  |  tiers {snap.tiers}")
    print(f"ladder {L.by_step}  |  PDFs read {L.pdfs_read}  |  LLM: {L.llm_status} ({L.llm_calls} calls, {L.llm_filings} filings)")
    print(f"never touched a model: {L.never_touched_model}/{L.filings} ({L.never_touched_model_pct:.1%})")
    print(f"cards {snap.cards}  |  LLM usage: {snap.llm_usage}")
    print(f"wrote {path}\n")
    for e in snap.events[: args.show]:
        parts = " ".join(f"{p.part[:4]}{p.points:+.0f}" for p in e.breakdown if p.points)
        print(f"{e.tier:9s} {e.score:5.1f}  {e.symbol:10s} {e.occurred_at:%d %b} {(e.summary or e.title)[:62]:62s} [{parts}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
