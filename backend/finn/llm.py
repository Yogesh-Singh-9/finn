"""LLM access. Models extract or write prose; they never compute numbers.

Two tiers, each an ordered fallback chain read from env (swap models without code):

    FINN_LLM_CHEAP   = gemini-3.5-flash-lite,groq:llama-3.3-70b-versatile
    FINN_LLM_STRONG  = gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash,groq:llama-3.3-70b-versatile
    FINN_LLM_TIMEOUT_S = 45

A link is "<provider>:<model>"; a bare name means gemini. Each link gets the timeout and
one backoff retry on 429/503 ("high demand"). A link that fails twice in a process is
skipped for the rest of it (circuit breaker), so a dead model costs ~45s once, not per call.

Honest cache. The key is sha256(model | prompt_version | system | user) of the model that
*actually answered*, and the entry records provider/model. Lookup walks the chain: a link's
cache is consulted only when every link above it has failed live (or is breaker-open) in
this process. So a fallback answer is never served as the primary's, and once the primary
recovers it gets asked. Failed / invalid answers are never written.

    result, meta = llm.json_call_meta(tier="cheap", purpose=..., prompt_version=..., system=...,
                                      user=..., schema=GEMINI_SCHEMA, result_type=PydanticModel)

Raises LLMUnavailable when no link is configured or all fail; callers fail soft.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from . import config

log = logging.getLogger("finn.llm")
Tier = Literal["cheap", "strong"]

_DEFAULT_CHAINS: dict[str, str] = {
    "cheap": "gemini-3.5-flash-lite,groq:llama-3.3-70b-versatile",
    "strong": "gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash,groq:llama-3.3-70b-versatile",
}
TIMEOUT_S = float(os.getenv("FINN_LLM_TIMEOUT_S", "45"))
BACKOFF_S = float(os.getenv("FINN_LLM_BACKOFF_S", "4"))
BREAKER_FAILS = 2
# Paid-tier list prices (USD per 1M tokens in/out), matched by model-name prefix. We run on
# free tiers, so this is a "what it would cost" estimate for the UI, not a bill.
PRICE_PREFIX: list[tuple[str, tuple[float, float]]] = [
    ("gemini-3.5-flash-lite", (0.10, 0.40)), ("gemini-", (0.30, 2.50)), ("llama-", (0.59, 0.79)),
]
CACHE_DIR = config.DATA_DIR / "llm_cache"
CALL_LOG = CACHE_DIR / "calls.jsonl"


class LLMUnavailable(Exception):
    pass


class _Retryable(Exception):
    pass


@dataclass(frozen=True)
class Link:
    provider: Literal["gemini", "groq"]
    model: str

    @property
    def name(self) -> str:
        return f"{self.provider}:{self.model}"


def _parse_chain(spec: str) -> list[Link]:
    out: list[Link] = []
    for raw in spec.split(","):
        raw = raw.strip()
        if not raw:
            continue
        prov, _, model = raw.partition(":") if ":" in raw else ("gemini", "", raw)
        if prov in ("gemini", "groq") and model:
            out.append(Link(prov, model))  # type: ignore[arg-type]
    return out


def chain(tier: Tier) -> list[Link]:
    env = os.getenv(f"FINN_LLM_{tier.upper()}") or _DEFAULT_CHAINS[tier]
    return [l for l in _parse_chain(env) if _key(l.provider)]


def _key(provider: str) -> str | None:
    v = os.getenv("GEMINI_API_KEY" if provider == "gemini" else "GROQ_API_KEY", "").strip()
    return v or None


def available(tier: Tier = "cheap") -> bool:
    return bool(chain(tier))


# ---------------------------------------------------------------- usage / breaker state
@dataclass
class Usage:
    calls: int = 0
    cached: int = 0
    fallbacks: int = 0
    failures: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    by_model: dict[str, int] = field(default_factory=dict)

    def line(self) -> str:
        return (f"{self.calls} calls, {self.cached} cached, {self.fallbacks} via fallback, "
                f"${self.cost_usd:.4f} est.")


USAGE = Usage()
_fails: dict[str, int] = {}


# A live (API) build sets a wall-clock deadline per tier: once it passes, live calls on
# that tier are skipped (cache still read) so the build returns without them. Fail soft.
DEADLINES: dict[str, float] = {}


def set_deadline(tier: str, seconds: float | None) -> None:
    if seconds is None:
        DEADLINES.pop(tier, None)
    else:
        DEADLINES[tier] = time.monotonic() + seconds


def reset_breakers() -> None:
    """A long-running server starts every build with a clean circuit breaker."""
    _fails.clear()


def reset_usage() -> None:
    global USAGE
    USAGE = Usage()


@dataclass
class CallMeta:
    provider: str
    model: str
    cached: bool
    fallback: bool  # answered by a link below the first configured one
    skipped: list[str]  # links that failed or were breaker-open before this answer


def _log_call(entry: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with CALL_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _cost(model: str, tin: int, tout: int) -> float:
    for prefix, (pin, pout) in PRICE_PREFIX:
        if model.startswith(prefix):
            return round((tin * pin + tout * pout) / 1e6, 6)
    return 0.0


# ---------------------------------------------------------------- providers
def _post(url: str, headers: dict, body: dict) -> dict:
    # httpx's timeout is per read, so a server trickling bytes can hold a call for minutes
    # (seen: 175s on flash-lite). The wall-clock limit is enforced here, on a daemon thread
    # so an abandoned request never blocks the process from exiting.
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["r"] = httpx.post(url, headers=headers, json=body, timeout=TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 - re-raised below
            box["e"] = e

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(TIMEOUT_S)
    if th.is_alive() or isinstance(box.get("e"), httpx.TimeoutException):
        raise RuntimeError(f"timeout after {TIMEOUT_S:.0f}s")
    if "e" in box:
        raise box["e"]
    r = box["r"]
    if r.status_code in (429, 500, 502, 503, 504):
        raise _Retryable(f"{r.status_code}: {r.text[:160]}")
    if r.status_code != 200:
        raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
    return r.json()


def _gemini(model: str, system: str, user: str, schema: dict | None) -> tuple[str, int, int]:
    gen: dict[str, Any] = {"temperature": 0, "responseMimeType": "application/json"}
    if schema:
        gen["responseSchema"] = schema
    j = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
              {"x-goog-api-key": _key("gemini") or ""},  # key in a header, never in the URL
              {"systemInstruction": {"parts": [{"text": system}]},
               "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": gen})
    cand = (j.get("candidates") or [{}])[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text:
        raise RuntimeError(f"empty answer (finishReason={cand.get('finishReason')})")
    u = j.get("usageMetadata", {})
    return text, int(u.get("promptTokenCount", 0)), int(u.get("candidatesTokenCount", 0))


def _groq(model: str, system: str, user: str, schema: dict | None) -> tuple[str, int, int]:
    sys_msg = system + ("\nReturn JSON matching this schema:\n" + json.dumps(schema) if schema else "")
    j = _post("https://api.groq.com/openai/v1/chat/completions",
              {"Authorization": f"Bearer {_key('groq') or ''}"},
              {"model": model, "temperature": 0, "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": sys_msg}, {"role": "user", "content": user}]})
    u = j.get("usage", {})
    return j["choices"][0]["message"]["content"], int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))


def _ask(link: Link, system: str, user: str, schema: dict | None) -> tuple[str, int, int]:
    fn = _gemini if link.provider == "gemini" else _groq
    try:
        return fn(link.model, system, user, schema)
    except _Retryable as e:
        log.info("%s busy (%s), retrying in %.0fs", link.name, str(e)[:60], BACKOFF_S)
        time.sleep(BACKOFF_S)
        try:
            return fn(link.model, system, user, schema)
        except _Retryable as e2:
            raise RuntimeError(f"busy twice: {e2}") from e2


# ---------------------------------------------------------------- cached call
def _cache_path(link: Link, prompt_version: str, system: str, user: str):
    key = hashlib.sha256(f"{link.name}|{prompt_version}|{system}|{user}".encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{key[:32]}.json"


def json_call_meta(*, tier: Tier, purpose: str, prompt_version: str, system: str, user: str,
                   schema: dict | None, result_type: Any, unwrap: str | None = None) -> tuple[Any, CallMeta]:
    """Structured call through the tier's chain. `result_type` is a Pydantic model or a
    typing type the JSON must validate against. Groq's json_object mode needs an object at
    top level, so array results are requested wrapped as {unwrap: [...]}."""
    links = chain(tier)
    if not links:
        raise LLMUnavailable(f"no {tier} model configured (check keys in backend/.env)")
    adapter = TypeAdapter(result_type)
    errors: list[str] = []
    skipped: list[str] = []
    now = lambda: datetime.now().isoformat(timespec="seconds")  # noqa: E731

    for i, link in enumerate(links):
        path = _cache_path(link, prompt_version, system, user)
        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                result = adapter.validate_python(cached["result"])
            except (ValueError, KeyError, ValidationError):
                path.unlink(missing_ok=True)  # stale shape: drop and ask live
            else:
                USAGE.calls += 1
                USAGE.cached += 1
                USAGE.fallbacks += int(i > 0)
                _log_call({"ts": now(), "purpose": purpose, "model": link.name, "cached": True,
                           "fallback": i > 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0})
                return result, CallMeta(link.provider, link.model, True, i > 0, skipped)

        if _fails.get(link.name, 0) >= BREAKER_FAILS:
            skipped.append(f"{link.name} (breaker open)")
            continue
        if tier in DEADLINES and time.monotonic() > DEADLINES[tier]:
            skipped.append(f"{link.name} (time budget spent)")
            errors.append(f"{link.name}: {tier} time budget spent")
            continue
        t0 = time.monotonic()
        try:
            text, tin, tout = _ask(link, system, user, schema)
            data = json.loads(text)
            if unwrap and isinstance(data, dict):
                data = data.get(unwrap, data)
            result = adapter.validate_python(data)
        except (RuntimeError, httpx.HTTPError, json.JSONDecodeError, ValidationError, KeyError, IndexError) as e:
            _fails[link.name] = _fails.get(link.name, 0) + 1
            msg = f"{link.name}: {type(e).__name__}: {str(e)[:160]}"
            errors.append(msg)
            skipped.append(link.name)
            log.warning("llm link failed: %s", msg)
            _log_call({"ts": now(), "purpose": purpose, "model": link.name, "cached": False, "ok": False,
                       "error": msg[:240], "latency_s": round(time.monotonic() - t0, 2)})
            continue

        _fails[link.name] = 0
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"provider": link.provider, "model": link.model, "purpose": purpose,
                                    "prompt_version": prompt_version, "created": now(),
                                    "result": adapter.dump_python(result, mode="json")}, ensure_ascii=False),
                        encoding="utf-8")
        cost = _cost(link.model, tin, tout)
        USAGE.calls += 1
        USAGE.fallbacks += int(i > 0)
        USAGE.tokens_in += tin
        USAGE.tokens_out += tout
        USAGE.cost_usd += cost
        USAGE.by_model[link.name] = USAGE.by_model.get(link.name, 0) + 1
        _log_call({"ts": now(), "purpose": purpose, "model": link.name, "cached": False, "ok": True,
                   "fallback": i > 0, "tokens_in": tin, "tokens_out": tout,
                   "latency_s": round(time.monotonic() - t0, 2), "cost_usd": cost})
        return result, CallMeta(link.provider, link.model, False, i > 0, skipped)

    USAGE.failures += 1
    raise LLMUnavailable("; ".join(errors) or "all links breaker-open")


def json_call(**kw: Any) -> Any:
    return json_call_meta(**kw)[0]


__all__ = ["json_call", "json_call_meta", "available", "chain", "LLMUnavailable", "USAGE", "reset_usage",
           "CallMeta", "BaseModel"]
