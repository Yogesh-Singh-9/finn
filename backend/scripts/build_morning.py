"""Write data/snapshots/morning.json from brief.json. Run from backend/ after build_brief.py:

    .venv/Scripts/python scripts/build_morning.py [--no-llm]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finn import config  # noqa: E402
from finn.brief.morning import build, write  # noqa: E402
from finn.models import BriefSnapshot  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    brief = BriefSnapshot.model_validate_json((config.SNAPSHOT_DIR / "brief.json").read_text(encoding="utf-8"))
    call = build(brief, use_llm=not args.no_llm)
    path = write(call)
    for c in call.chapters:
        print(f"[{c.title}]")
        for s in c.sentences:
            print("  ", s)
    print(f"\n{call.words} words, ~{call.est_seconds}s · {call.by} · {call.llm_usage}")
    if call.dropped:
        print("dropped (number not in facts):", *call.dropped, sep="\n  ")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
