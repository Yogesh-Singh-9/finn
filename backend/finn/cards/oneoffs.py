"""One-offs in a results filing: exceptional items and the company's own "excluding /
non-operational / one-time ... ₹X crore" language.

1. Rules find candidate passages (keyword + crore amount). No model if there are none.
2. flash-lite names each one-off: description, amount as written, the exact sentence.
3. Python verifies the quote and amount verbatim (OCR-tolerant) and drops duty / price /
   tax effects, which are business conditions rather than one-offs.
4. The company's adjusted figure is found by rules, not the model: a number in the
   company's own text, next to "profit before/after tax", that equals reported minus the
   one-off (within 1.5%). That makes it the company's stated figure *and* consistent with
   the one-off. The same row's prior-year number becomes the base when it matches
   screener's; growth is computed here.

TITAN Q2 FY27 is the test: ₹671 cr non-operational Ind-AS credit; the company's own table
row "Profit after Tax 1,120 1,722 54%" and "Profit before Tax was ~2,329 crores".
"""
from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic import BaseModel

from .. import llm
from ..classify.pdftext import first_pages_text
from ..http import Fetcher
from ..models import AdjustedMetric, OneOff

log = logging.getLogger("finn.cards.oneoffs")
PROMPT_VERSION = "oneoff-v4"
_KEY = re.compile(r"exclud\w*|adjusted for|adjusting for|one-?time|non-?operational|exceptional item|"
                  r"one-?off|ind-?as credit|fair value (gain|change)", re.I)
_AMT = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:crores?|crs?\b|cr\.)", re.I)
# Business conditions, not one-offs (the company's own "adjusted" numbers keep them in).
_NOT_ONEOFF = re.compile(r"\b(custom|customs|duty|duties|commodity|gold price|price increase|tax rate)\b", re.I)
_METRIC_WORDS = {"pat": re.compile(r"profit after tax|\bpat\b|net profit", re.I),
                 "pbt": re.compile(r"profit before tax|\bpbt\b", re.I)}

_SYSTEM = (
    "You read an Indian listed company's quarterly results filing. Answer only from the excerpts; copy text and "
    "numbers exactly as written; never compute. List the one-off items for the CURRENT quarter: items the company "
    "itself calls exceptional, one-time or non-operational, or that it excludes from its own representation of "
    "profits. Each: description (short, as written), amount (as written, in crore), affects "
    "(profit | revenue | both | unclear), effect (gain if it raised reported profit, charge if it lowered it, "
    "else unclear), quote (the exact sentence that states the amount). "
    "Return {\"one_offs\": [...]}, empty if none."
)
_SCHEMA = {
    "type": "OBJECT",
    "properties": {"one_offs": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "description": {"type": "STRING"}, "amount": {"type": "STRING"},
        "affects": {"type": "STRING", "enum": ["profit", "revenue", "both", "unclear"]},
        "effect": {"type": "STRING", "enum": ["gain", "charge", "unclear"]},
        "quote": {"type": "STRING"}}, "required": ["description", "amount", "quote"]}}},
    "required": ["one_offs"],
}


class _O(BaseModel):
    description: str
    amount: str
    affects: Literal["profit", "revenue", "both", "unclear"] = "unclear"
    effect: Literal["gain", "charge", "unclear"] = "unclear"
    quote: str


def _norm(s: str | None) -> str:
    """OCR-tolerant: letters/digits only. Rupee glyphs come out as '{ or 'f, so drop
    them with the punctuation; join digit groups ("1, 793" -> "1793")."""
    s = (s or "").lower().replace("₹", " ")
    s = re.sub(r"(?<=\d),\s?(?=\d{3}\b)", "", s)
    return re.sub(r"[^a-z0-9.]+", " ", s).replace(" .", " ").strip()


def _num(s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s.replace(", ", ","))
    return float(m.group().replace(",", "")) if m else None


def _nums_in(norm: str) -> list[float]:
    return [float(n) for n in re.findall(r"\d+(?:\.\d+)?", norm)]


def candidates(text: str, window: int = 1600, cap: int = 9000) -> str:
    """Passages around one-off language that carry a crore amount."""
    spans: list[tuple[int, int]] = []
    for m in _KEY.finditer(text):
        lo, hi = max(0, m.start() - window), min(len(text), m.end() + 400)
        if not _AMT.search(text[max(0, m.start() - 300):hi]):
            continue
        if spans and lo <= spans[-1][1]:
            spans[-1] = (spans[-1][0], hi)
        else:
            spans.append((lo, hi))
    return "\n…\n".join(re.sub(r"[ \t]+", " ", text[a:b]) for a, b in spans)[:cap]


def _lines(text: str) -> list[str]:
    """Table rows and sentences: split on newlines, then on sentence ends."""
    out: list[str] = []
    for ln in text.splitlines():
        out += [s for s in re.split(r"(?<=[a-z%)])\.\s+(?=[A-Z•])", ln) if s.strip()]
    return out


def _nets(one_offs: list[OneOff]) -> list[float]:
    """Possible net profit effects (gain +, charge -). Unclear items are tried both ways."""
    nets = [0.0]
    for o in one_offs:
        if o.affects == "revenue":
            continue
        signs = [1] if o.effect == "gain" else [-1] if o.effect == "charge" else [1, -1]
        nets = [n + sg * o.amount_cr for n in nets for sg in signs]
    return [n for n in dict.fromkeys(nets) if n]


def _scan(docs: list[tuple[str, str]], words: re.Pattern, target: float, tol: float,
          base: float | None) -> tuple[str, str, float, float | None] | None:
    for url, text in docs:
        for ln in _lines(text):
            if not words.search(ln) or _NOT_ONEOFF.search(ln):
                continue
            nums = _nums_in(_norm(ln))
            for i, n in enumerate(nums):
                if n >= 10 and abs(n - target) <= tol:
                    stated = next((b for b in nums[:i] if base and abs(b - base) <= 0.01 * base), None)
                    return url, ln, n, stated
    return None


def find_adjusted(docs: list[tuple[str, str]], reported: dict[str, float | None], one_offs: list[OneOff],
                  prev_y: dict[str, float | None]) -> list[AdjustedMetric]:
    """The company's own adjusted PBT/PAT: a figure on a PBT/PAT line of its text equal to
    reported - net one-off effect. Tolerance is the tighter of 1.5% and 30% of the one-off,
    so a small one-off can't match the reported figure itself."""
    out: list[AdjustedMetric] = []
    for key, words in _METRIC_WORDS.items():
        rep = reported.get(key)
        if not rep:
            continue
        hit = None
        for net in _nets(one_offs):
            target = rep - net
            if target > 0 and not hit:
                hit = _scan(docs, words, target, min(0.015 * target, 0.3 * abs(net)), prev_y.get(key))
        if not hit:
            continue
        url, ln, val, stated_base = hit
        base = stated_base if stated_base is not None else prev_y.get(key)
        out.append(AdjustedMetric(
            key=key, label=f"{key.upper()} (company's adjusted)", value=val, prev_y=base,
            prev_y_source="company" if stated_base is not None else "screener",
            yoy=round(val / base - 1, 5) if base and base > 0 else None,
            quote=re.sub(r"\s+", " ", ln).strip()[:300], source=url))
    return out


def detect(urls: list[str], xbrl_quarter: dict | None, reported: dict[str, float | None],
           prev_y: dict[str, float | None], fetcher: Fetcher, use_llm: bool = True,
           ) -> tuple[list[OneOff], list[AdjustedMetric], str | None]:
    """(one_offs, adjusted, extracted_by). reported / prev_y: card values by metric key."""
    one_offs: list[OneOff] = []
    exc = (xbrl_quarter or {}).get("ExceptionalItemsBeforeTax")
    if exc:
        one_offs.append(OneOff(description="Exceptional items (statement line)", amount_cr=abs(exc),
                               affects="profit", effect="gain" if exc > 0 else "charge", quote=f"ExceptionalItemsBeforeTax = {exc:,.2f} crore",
                               source="NSE XBRL", verified=True))
    docs: list[tuple[str, str]] = []
    for u in dict.fromkeys(urls):
        t = first_pages_text(fetcher, u, pages=14, max_chars=80000)
        if t:
            docs.append((u, t))
    excerpt = "\n\n".join(c for _, t in docs if (c := candidates(t)))
    by = None
    if excerpt and use_llm and llm.available("cheap"):
        try:
            got, meta = llm.json_call_meta(tier="cheap", purpose="oneoffs", prompt_version=PROMPT_VERSION,
                                           system=_SYSTEM, user=excerpt, schema=_SCHEMA, result_type=list[_O],
                                           unwrap="one_offs")
            by = f"{meta.provider}:{meta.model}"
        except llm.LLMUnavailable as e:
            log.warning("one-off extraction unavailable: %s", e)
            got = []
        for o in got:
            amt, qn = _num(o.amount), _norm(o.quote)
            src = next((u for u, t in docs if qn and qn in _norm(t)), None)
            ok = (amt is not None and src is not None and any(abs(n - amt) < 0.01 for n in _nums_in(qn))
                  and _KEY.search(o.quote) and not _NOT_ONEOFF.search(o.quote + " " + o.description))
            if not ok:
                log.info("one-off dropped: %s", o.quote[:90])
                continue
            if any(abs(x.amount_cr - amt) < 0.5 for x in one_offs):
                continue
            one_offs.append(OneOff(description=o.description, amount_cr=amt, affects=o.affects, effect=o.effect,
                                   quote=re.sub(r"\s+", " ", o.quote).strip(), source=src, verified=True))
    adjusted = find_adjusted(docs, reported, one_offs, prev_y) if one_offs else []
    return one_offs, adjusted, by
