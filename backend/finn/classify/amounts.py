"""Rupee amounts in filing text -> INR crore. Python only; no model involved.

Only INR is converted. Foreign-currency amounts are reported but not converted
(no FX guessing). The largest INR amount is used for the materiality ratio."""
from __future__ import annotations

import re

_NUM = r"(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_INR = re.compile(rf"(?:₹|rs\.?|inr|rupees)\s*{_NUM}\s*(crores?|cr\.?|lakhs?|lacs?|million|mn|billion|bn)?\b", re.I)
_INR_SUFFIX = re.compile(rf"{_NUM}\s*(crores?|cr\b|lakhs?)\b", re.I)
_FX = re.compile(rf"(usd|us\$|\$|eur|€|gbp|£)\s*{_NUM}\s*(million|mn|billion|bn)?", re.I)

_MULT = {"crore": 1.0, "crores": 1.0, "cr": 1.0, "cr.": 1.0, "lakh": 0.01, "lakhs": 0.01, "lac": 0.01,
         "lacs": 0.01, "million": 0.1, "mn": 0.1, "billion": 100.0, "bn": 100.0}


def _val(num: str, unit: str | None) -> float | None:
    try:
        v = float(num.replace(",", ""))
    except ValueError:
        return None
    if unit is None:
        return v / 1e7 if v >= 1e5 else None  # bare rupees, only if clearly a rupee figure
    return v * _MULT.get(unit.lower().rstrip("."), 0) or None


def inr_crore_amounts(text: str) -> list[float]:
    out: list[float] = []
    for m in _INR.finditer(text):
        v = _val(m.group(1), m.group(2))
        if v:
            out.append(round(v, 2))
    for m in _INR_SUFFIX.finditer(text):
        v = _val(m.group(1), m.group(2))
        if v and round(v, 2) not in out:
            out.append(round(v, 2))
    return out


_BAND_LABELS = re.compile(r"lassification\s+((?:[A-Z][\w-]+\s+){2,8})\s*Value in\s*(?:₹|Rs\.?|INR)\s*Cr\.?\s+([^\n]+)", re.I)
_RANGE = re.compile(r"([\d,]+)\s*(?:to|-|–)\s*([\d,]+)|>\s*([\d,]+)")


def band_amount(title: str, pdf_text: str | None) -> tuple[float, str] | None:
    """Order-size bands, e.g. title '(Major*)' + a footnote table
    'Classification Significant Large Major ... / Value in ₹ Cr 1,000 to 2,500 ...'.
    Returns (lower bound in crore, note). Generic: any company that publishes such a table."""
    if not pdf_text:
        return None
    m = _BAND_LABELS.search(pdf_text)
    if not m:
        return None
    labels = [x for x in m.group(1).split() if x.lower() != "classification"]
    ranges = [(float((a or c).replace(",", "")), float(b.replace(",", "")) if b else None)
              for a, b, c in _RANGE.findall(m.group(2))]
    if len(labels) != len(ranges):
        return None
    for label, (lo, hi) in zip(labels, ranges):
        if re.search(rf"\(\s*\*?\s*{re.escape(label)}\s*\*?\s*\)", title, re.I):
            rng = f"₹{lo:,.0f}–{hi:,.0f} cr" if hi else f">₹{lo:,.0f} cr"
            return lo, f"'{label}' band ({rng}); lower bound used"
    return None


def fx_mentions(text: str) -> list[str]:
    return [m.group(0).strip() for m in _FX.finditer(text)][:3]
