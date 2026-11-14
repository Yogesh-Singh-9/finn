"""LLM access: Gemini (flash-lite / flash) with Groq fallback, content-hash cache,
call log. Models extract or write prose; they never compute numbers.

    llm.json_call(tier="lite", purpose="classify", prompt_version="cls-v1",
                  system=..., user=..., schema=GEMINI_SCHEMA, model=PydanticModel)

Raises LLMUnavailable when no provider is configured or all fail; callers fail soft.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Literal, TypeVar

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from . import config

log = logging.getLogger("finn.llm")
T = TypeVar("T")
Tier = Literal["lite", "flash"]

MODELS: dict[Tier, str] = {
    "lite": os.getenv("FINN_GEMINI_LITE_MODEL", "gemini-2.5-flash-lite"),
    "flash": os.getenv("FINN_GEMINI_MODEL", "gemini-2.5-flash"),
}
GROQ_MODEL = os.getenv("FINN_GROQ_MODEL", "llama-3.3-70b-versatile")
# Paid-tier list prices (USD per 1M tokens in/out). We run on free tiers, so this is a
# "what it would cost" estimate shown in the UI, not an actual bill.
PRICE: dict[str, tuple[float, float]] = {
    MODELS["lite"]: (0.10, 0.40),
    MODELS["flash"]: (0.30, 2.50),
    GROQ_MODEL: (0.59, 0.79),
}
CACHE_DIR = config.DATA_DIR / "llm_cache"
CALL_LOG = CACHE_DIR / "calls.jsonl"


class LLMUnavailable(Exception):
    pass


def available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GROQ_API_KEY"))


def _log_call(entry: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with CALL_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _cost(model: str, tin: int, tout: int) -> float:
    pin, pout = PRICE.get(model, (0.0, 0.0))
    return round((tin * pin + tout * pout) / 1e6, 6)


def _gemini(model: str, system: str, user: str, schema: dict | None) -> tuple[str, int, int]:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise LLMUnavailable("no GEMINI_API_KEY")
    gen: dict[str, Any] = {"temperature": 0, "responseMimeType": "application/json"}
    if schema:
        gen["responseSchema"] = schema
    r = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key},  # key in a header, never in the URL
        json={"systemInstruction": {"parts": [{"text": system}]},
              "contents": [{"role": "user", "parts": [{"text": user}]}],
              "generationConfig": gen},
        timeout=90,
    )
    if r.status_code != 200:
        raise RuntimeError(f"gemini {r.status_code}: {r.text[:200]}")
    j = r.json()
    text = j["candidates"][0]["content"]["parts"][0]["text"]
    u = j.get("usageMetadata", {})
    return text, int(u.get("promptTokenCount", 0)), int(u.get("candidatesTokenCount", 0))


def _groq(system: str, user: str, schema: dict | None) -> tuple[str, int, int]:
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise LLMUnavailable("no GROQ_API_KEY")
    sys_msg = system + ("\nReturn JSON matching this schema:\n" + json.dumps(schema) if schema else "")
    r = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": GROQ_MODEL, "temperature": 0, "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": sys_msg}, {"role": "user", "content": user}]},
        timeout=90,
    )
    if r.status_code != 200:
        raise RuntimeError(f"groq {r.status_code}: {r.text[:200]}")
    j = r.json()
    u = j.get("usage", {})
    return j["choices"][0]["message"]["content"], int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))


def json_call(*, tier: Tier, purpose: str, prompt_version: str, system: str, user: str,
              schema: dict | None, result_type: Any, unwrap: str | None = None) -> Any:
    """Cached structured call. `result_type` is a Pydantic model or typing type the JSON
    must validate against. Groq's json_object mode needs an object at top level, so
    array results are requested wrapped as {unwrap: [...]}."""
    model = MODELS[tier]
    key = hashlib.sha256(f"{model}|{prompt_version}|{system}|{user}".encode()).hexdigest()
    path = CACHE_DIR / f"{key[:32]}.json"
    adapter = TypeAdapter(result_type)

    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        _log_call({"ts": datetime.now().isoformat(timespec="seconds"), "purpose": purpose, "model": model,
                   "provider": cached.get("provider"), "tokens_in": 0, "tokens_out": 0, "cached": True, "cost_usd": 0.0})
        return adapter.validate_python(cached["result"])

    if not available():
        raise LLMUnavailable("no LLM keys configured in backend/.env")

    errors: list[str] = []
    for provider in ("gemini", "groq"):
        try:
            t0 = time.monotonic()
            if provider == "gemini":
                text, tin, tout = _gemini(model, system, user, schema)
                used_model = model
            else:
                text, tin, tout = _groq(system, user, schema)
                used_model = GROQ_MODEL
            data = json.loads(text)
            if unwrap and isinstance(data, dict):
                data = data.get(unwrap, data)
            result = adapter.validate_python(data)
        except (LLMUnavailable, RuntimeError, httpx.HTTPError, json.JSONDecodeError, ValidationError, KeyError) as e:
            errors.append(f"{provider}: {type(e).__name__}: {str(e)[:160]}")
            continue
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        dumped = adapter.dump_python(result, mode="json")
        path.write_text(json.dumps({"provider": provider, "model": used_model, "purpose": purpose,
                                    "prompt_version": prompt_version, "result": dumped}), encoding="utf-8")
        _log_call({"ts": datetime.now().isoformat(timespec="seconds"), "purpose": purpose, "model": used_model,
                   "provider": provider, "tokens_in": tin, "tokens_out": tout, "cached": False,
                   "latency_s": round(time.monotonic() - t0, 2), "cost_usd": _cost(used_model, tin, tout)})
        return result
    raise LLMUnavailable("; ".join(errors))


__all__ = ["json_call", "available", "LLMUnavailable", "MODELS", "BaseModel"]
