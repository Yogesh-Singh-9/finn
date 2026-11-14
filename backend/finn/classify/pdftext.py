"""First-page text of a filing PDF: the cheapest way to rescue a useless subject.
Cover letters on page 1 almost always carry a 'Sub:' line that says what it is."""
from __future__ import annotations

import io
import logging
import re

from ..http import FetchError, Fetcher

log = logging.getLogger("finn.pdf")
_SUB_RE = re.compile(r"(?:^|\n)\s*(?:Sub(?:ject)?\s*[:\-–]|Re\s*:)\s*(.{10,300}?)(?:\n\s*\n|Dear|Ref\b|Pursuant|In terms|We wish|This is|With reference|$)", re.I | re.S)
# Fallback when there's no Sub line: the sentence after "inform you that".
_GIST_RE = re.compile(r"(?:inform(?:ed)?\s+(?:you\s+)?that|wish to inform you that|hereby inform that|please note that)\s+(.{20,240}?[.;])", re.I | re.S)
_BOILER_SUB = re.compile(r"^.{0,40}?\b(disclosure|intimation)\b.{0,200}?regulations?,?\s*20\d\d\s*(\([^)]{0,60}\))?\s*[:\-–—]?\s*", re.I)


def gist_line(text: str | None) -> str | None:
    if not text:
        return None
    m = _GIST_RE.search(text)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip(" .;")[:200]
    # long sentence: take what follows "inform (you) that" and cut at a word boundary
    m = re.search(r"inform(?:ed)?\s+(?:you\s+)?that\s+(.{20,400})", text, re.I | re.S)
    if not m:
        return None
    s = re.sub(r"\s+", " ", m.group(1))
    return (s[:180].rsplit(" ", 1)[0] + "…") if len(s) > 180 else s


def first_pages_text(f: Fetcher, url: str | None, pages: int = 2, max_chars: int = 5000) -> str | None:
    if not url or not url.lower().split("?")[0].endswith(".pdf"):
        return None
    try:
        data = f.get_bytes(url, cache="forever")
    except FetchError as e:
        log.info("pdf fetch failed %s", e)
        return None
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((reader.pages[i].extract_text() or "") for i in range(min(pages, len(reader.pages))))
    except Exception as e:  # malformed / encrypted PDFs
        log.info("pdf parse failed %s: %s", url, e)
        return None
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text[:max_chars] or None  # scanned PDFs yield "" -> None


def subject_line(text: str | None) -> str | None:
    if not text:
        return None
    m = _SUB_RE.search(text)
    if not m:
        return None
    s = re.sub(r"\s+", " ", m.group(1)).strip(" .:-")
    # "Disclosure under Regulation 30 of SEBI (LODR) Regulations, 2015: Outcome of the
    # Board meeting" -> keep only what follows the boilerplate.
    s = _BOILER_SUB.sub("", s).strip(" .:-“”\"(")
    return s[:200] or None
