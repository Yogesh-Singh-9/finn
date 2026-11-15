"""FastAPI: live research for a ticker FINN has never seen.

The frontend reads precomputed snapshots first (data/snapshots/research/<SYM>.json ships
with the bundle) and only calls this for a new ticker. A build runs in a thread and
reports stages, so the UI can say "reading 4 transcripts..." instead of spinning.

    .venv/Scripts/uvicorn finn.api:app --port 8000
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .http import Fetcher
from .research import dossier
from .sources.screener import fetch_fundamentals

app = FastAPI(title="FINN", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])

_SYM = re.compile(r"^[A-Z0-9&\-]{1,20}$")
_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()          # one build at a time: free-tier rate limits


def _sym(s: str) -> str:
    s = s.upper().strip()
    if not _SYM.match(s):
        raise HTTPException(400, "not an NSE symbol")
    return s


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/research/{symbol}")
def research(symbol: str) -> Any:
    path = dossier.RESEARCH_DIR / f"{_sym(symbol)}.json"
    if not path.exists():
        raise HTTPException(404, "not built yet")
    return json.loads(path.read_text(encoding="utf-8"))


def _run(job_id: str, sym: str) -> None:
    job = _jobs[job_id]

    def stage(s: str) -> None:
        job["stages"].append(s)
        job["stage"] = s

    with _lock:
        try:
            with Fetcher() as f:
                stage("Fetching screener's tables…")
                path = config.PROCESSED_DIR / "fundamentals" / f"{sym}.json"
                fund, status = fetch_fundamentals(f, sym)
                if fund is not None:
                    data = fund.model_dump(mode="json")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
                elif path.exists():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    stage("screener unavailable; using the last saved tables")
                else:
                    raise RuntimeError(f"screener has no page for {sym} ({status.error})")
                snap = dossier.build(sym, f, data, progress=stage)
                dossier.write(snap)
            job.update(status="done", stage="Done")
        except Exception as e:  # noqa: BLE001 - report, never crash the server
            job.update(status="error", error=f"{type(e).__name__}: {str(e)[:200]}")


@app.post("/api/research/{symbol}/build")
def build(symbol: str) -> dict:
    sym = _sym(symbol)
    for jid, j in _jobs.items():
        if j["symbol"] == sym and j["status"] == "running":
            return {"job_id": jid}
    jid = uuid.uuid4().hex[:12]
    _jobs[jid] = {"symbol": sym, "status": "running", "stage": "Queued", "stages": [], "started": time.time()}
    threading.Thread(target=_run, args=(jid, sym), daemon=True).start()
    return {"job_id": jid}


@app.get("/api/jobs/{job_id}")
def job(job_id: str) -> dict:
    j = _jobs.get(job_id)
    if not j:
        raise HTTPException(404, "unknown job")
    return {k: v for k, v in j.items() if k != "started"} | {"elapsed_s": round(time.time() - j["started"])}
