"""Polite HTTP: browser UA, per-host rate limit, retry with backoff, disk cache.

Cache policy per call:
  "daily"   - keyed by URL + today's date (listings that change)
  "forever" - keyed by URL only (immutable files: XBRL, PDFs)
  None      - no cache
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from . import config

log = logging.getLogger("finn.http")

CachePolicy = Literal["daily", "forever"] | None
RETRY_STATUS = {429, 500, 502, 503, 504}


class FetchError(Exception):
    def __init__(self, url: str, reason: str, status: int | None = None):
        super().__init__(f"{reason} [{status}] {url}")
        self.url, self.reason, self.status = url, reason, status


class Fetcher:
    def __init__(self, cache_dir: Path | None = None, retries: int = 3):
        self.cache_dir = cache_dir or (config.RAW_DIR / "http")
        self.retries = retries
        self._last_hit: dict[str, float] = {}
        self.client = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={
                "User-Agent": config.USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
                # NSE serves brotli we can't decode reliably; never advertise br.
                "Accept-Encoding": "gzip, deflate",
            },
        )
        self.redirects: dict[str, str] = {}  # requested host -> final host (diagnostics)

    # -- cache -----------------------------------------------------------
    def _cache_path(self, url: str, policy: CachePolicy) -> Path | None:
        if policy is None:
            return None
        key = url if policy == "forever" else f"{url}|{date.today().isoformat()}"
        h = hashlib.sha256(key.encode()).hexdigest()[:24]
        return self.cache_dir / urlparse(url).netloc / f"{h}.bin"

    # -- rate limit ------------------------------------------------------
    def _throttle(self, host: str) -> None:
        wait = config.MIN_INTERVAL_PER_HOST_S - (time.monotonic() - self._last_hit.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    # -- core ------------------------------------------------------------
    def get_bytes(self, url: str, *, cache: CachePolicy = "daily", headers: dict[str, str] | None = None) -> bytes:
        path = self._cache_path(url, cache)
        if path and path.exists():
            return path.read_bytes()

        host = urlparse(url).netloc
        last_err: FetchError | None = None
        for attempt in range(self.retries):
            self._throttle(host)
            try:
                r = self.client.get(url, headers=headers)
            except httpx.HTTPError as e:
                last_err = FetchError(url, f"network: {type(e).__name__}")
            else:
                final_host = r.url.host
                if final_host and final_host != host:
                    self.redirects[host] = final_host
                if r.status_code == 200:
                    if path:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(r.content)
                    return r.content
                last_err = FetchError(url, "http", r.status_code)
                if r.status_code not in RETRY_STATUS:
                    break  # 403/404: retrying won't help
            time.sleep(1.5 * 2**attempt)
        assert last_err is not None
        log.warning("fetch failed: %s", last_err)
        raise last_err

    def get_text(self, url: str, **kw: Any) -> str:
        return self.get_bytes(url, **kw).decode("utf-8", errors="replace")

    def get_json(self, url: str, **kw: Any) -> Any:
        kw.setdefault("headers", {"Accept": "application/json, text/plain, */*"})
        raw = self.get_bytes(url, **kw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            # Don't leave a poisoned cache entry behind (e.g. an HTML bot wall).
            path = self._cache_path(url, kw.get("cache", "daily"))
            if path and path.exists():
                path.unlink()
            raise FetchError(url, f"bad json: {raw[:80]!r}") from e

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
