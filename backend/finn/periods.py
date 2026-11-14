"""Indian fiscal periods. FY ends 31 March: Jun=Q1, Sep=Q2, Dec=Q3, Mar=Q4.

Sep 2026 -> FY27Q2, Mar 2026 -> FY26Q4. Annual "Mar 2026" -> FY26.
"""
from __future__ import annotations

from datetime import date, datetime

_Q_BY_MONTH = {6: 1, 9: 2, 12: 3, 3: 4}


def quarter_key(year: int, month: int) -> str | None:
    q = _Q_BY_MONTH.get(month)
    if q is None:
        return None
    fy = year + 1 if month >= 4 else year
    return f"FY{fy % 100:02d}Q{q}"


def fy_key(year: int, month: int) -> str:
    fy = year + 1 if month >= 4 else year
    return f"FY{fy % 100:02d}"


def parse_month_label(label: str) -> tuple[int, int] | None:
    """'Sep 2026' -> (2026, 9). Returns None for 'TTM' or junk."""
    try:
        d = datetime.strptime(label.strip(), "%b %Y")
    except ValueError:
        return None
    return d.year, d.month


def quarter_key_for_date(d: date) -> str | None:
    return quarter_key(d.year, d.month)
