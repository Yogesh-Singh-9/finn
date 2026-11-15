"""Static configuration. Demo symbols live here and nowhere else in logic."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

DATA_DIR = (BACKEND_DIR / os.getenv("FINN_DATA_DIR", "../data")).resolve()  # relative paths are from backend/, not the cwd
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
SNAPSHOT_DIR = DATA_DIR / "snapshots"

PORTFOLIO: list[str] = [
    "RELIANCE", "HDFCBANK", "BAJFINANCE", "INFY", "PERSISTENT", "TRENT",
    "TITAN", "DMART", "DIXON", "KAYNES", "POLYCAB", "LT", "HAL",
    "SUNPHARMA", "PIDILITIND", "TATAPOWER", "ETERNAL", "CDSL",
]
RESEARCH_DEMO: list[str] = ["KPITTECH", "ASTRAL", "DEEPAKNTR", "CAMS"]
# Promise Ledger showcase (need concall transcripts; TRENT has none).
LEDGER_DEMO: list[str] = ["TITAN", "POLYCAB", "INFY"]
BENCHMARK = "^NSEI"

# The brief snapshot covers this many days; the frontend filters 24h/7d/30d from it.
SNAPSHOT_DAYS = 30

# Base URL is config, not a constant: on GitHub runners nseindia.com/api redirects
# to nseindia.in. We always follow redirects and use attachment URLs as returned.
NSE_BASE = os.getenv("FINN_NSE_BASE", "https://www.nseindia.com")
SCREENER_BASE = os.getenv("FINN_SCREENER_BASE", "https://www.screener.in")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
MIN_INTERVAL_PER_HOST_S = 1.0

# Research demo order (config only, never logic): INFY leads, it has the guidance revision chain.
RESEARCH_DEMO_ORDER = ["INFY", "TITAN", "POLYCAB"]
