"""Classification ladder. Cheapest step that can decide, decides:

  1. NSE subject -> category (refined by regex within allowed edges)
  2. ordered regex on subject + one-line text
  3. page 1-2 of the PDF, when the subject/text is useless, a needed subtype is
     missing, or the score needs a rupee amount the text doesn't have
  4. flash-lite for leftovers, batched ~20 per call
  5. no model available -> 'unclassified/uncertain' and treated as MATERIAL
     (unsure between routine and material -> material)
"""
from __future__ import annotations

import logging
import re
from collections import Counter

from pydantic import BaseModel

from .. import llm
from ..http import Fetcher
from ..models import ClassifiedFiling, Filing
from . import amounts as amt
from . import pdftext
from .taxonomy import (CATEGORIES, GENERIC_SUBJECTS, NEEDS_AMOUNT, NEEDS_AMOUNT_SUBTYPES, NEEDS_SUBTYPE,
                       OVERRIDES, RULES, SUBJECT_MAP, mgmt_detail, people_in, subtype_for)

log = logging.getLogger("finn.classify")
PROMPT_VERSION = "cls-v1"
BATCH = 20

_BOILER = re.compile(
    r"^.*?(has informed the exchange|has submitted to the exchange|has filed with (the )?exchange|"
    r"has intimated the exchange)\s*(about|regarding|that|,)?\s*", re.I)
_USELESS = re.compile(r"^(general updates?|updates?|change in management|change in director\(s\) of the company|"
                      r"credit rating|bagging/receiving of orders/contracts|capacity addition|issue of securities|"
                      r"resignation of director/kmp/smp|action\(s\) initiated or orders passed|agreements?|"
                      r"corporate insolvency resolution process|statement of deviation.*|schedule of meet|transcript|"
                      r"link of recording|presentation|investor presentation|copy of newspaper publication|"
                      r"voting results|outcome of committee meeting.*|outcome of (the )?board meeting.*|information|"
                      r"outcome of (the )?special committee meeting.*|(disclosure|intimation) under regulations? .*|"
                      r"retirement|resignation|appointment|change in auditors of the company|reg 30 change in management|"
                      r"earnings presentation|a press release dated [^,]*)\.?$", re.I)


def clean_title(f: Filing) -> str:
    t = _BOILER.sub("", f.text).strip(" '\".")
    t = re.sub(r"\s+", " ", t)
    return (t[:1].upper() + t[1:]) if t else f.nse_subject


def is_useless(title: str, subject: str) -> bool:
    return len(title) < 12 or bool(_USELESS.match(title.strip())) or title.strip(". ").lower() == subject.lower()


def _deterministic(subject: str, text: str) -> tuple[str, str | None, str] | None:
    subj = subject.strip().lower()
    mapped = None if subj in GENERIC_SUBJECTS else SUBJECT_MAP.get(subj)
    if mapped:
        for cat, sub, pat in RULES:
            if (cat == mapped or (mapped, cat) in OVERRIDES) and pat.search(text):
                return cat, sub or subtype_for(cat, text), "subject+regex"
        return mapped, subtype_for(mapped, text), "subject"
    for cat, sub, pat in RULES:
        if pat.search(text):
            return cat, sub or subtype_for(cat, text), "regex"
    return None


class _LLMItem(BaseModel):
    id: str
    category: str
    subtype: str | None = None
    material: bool = True


_SCHEMA = {
    "type": "OBJECT",
    "properties": {"items": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "STRING"},
            "category": {"type": "STRING", "enum": list(CATEGORIES)},
            "subtype": {"type": "STRING", "nullable": True},
            "material": {"type": "BOOLEAN"},
        },
        "required": ["id", "category", "material"],
    }}},
    "required": ["items"],
}
_SYSTEM = (
    "You classify Indian stock-exchange filings into a fixed taxonomy. Use only the text given. "
    "Categories: results, M&A, fund_raise, management_change, litigation, insider_trading, earnings_call, "
    "investor_meetings, credit_rating, dividend, bonus_split, capex, press_release, postal_ballot, agm_egm, "
    "pledging, unclassified (routine paperwork). subtype: a short snake_case label or null. "
    "material=false only if you are confident it is routine paperwork; if unsure, material=true. "
    "Return {\"items\": [...]} with one item per input id."
)


class ClassifyStats(BaseModel):
    by_step: dict[str, int]
    pdfs_read: int
    llm_filings: int
    llm_batches: int
    llm_status: str


def classify(filings: list[Filing], fetcher: Fetcher, use_llm: bool = True) -> tuple[list[ClassifiedFiling], ClassifyStats]:
    out: list[ClassifiedFiling] = []
    leftovers: list[tuple[int, str]] = []  # (index in out, text for the model)
    pdfs_read = 0

    for f in filings:
        title = clean_title(f)
        useless = is_useless(title, f.nse_subject)
        # Rules see the text with NSE's "<Company> has informed the Exchange about"
        # prefix removed (so '^presentation' can anchor), never the subject string.
        text = title
        res = _deterministic(f.nse_subject, text)
        amounts = amt.inr_crore_amounts(f.text)
        fx = amt.fx_mentions(f.text)
        amount_note = None

        cat, sub, by = res if res else (None, None, None)
        need_pdf = (
            res is None
            or useless
            or (cat in NEEDS_SUBTYPE and sub is None)
            or ((cat in NEEDS_AMOUNT or sub in NEEDS_AMOUNT_SUBTYPES) and not amounts)
        )
        pdf_text = pdf_sub = None
        if need_pdf and f.attachment_url:
            # Order-size band tables sit at the end of the press release.
            pdf_text = pdftext.first_pages_text(fetcher, f.attachment_url, pages=4 if sub == "order_win" else 2,
                                                max_chars=12000 if sub == "order_win" else 5000)
            if pdf_text:
                pdfs_read += 1
                pdf_sub = pdftext.subject_line(pdf_text)
                # Sub line first (precise), then the whole first page (noisy).
                # When the one-liner is useless, whatever it matched can't be trusted:
                # the PDF result replaces it.
                for extra in ([pdf_sub] if pdf_sub else []) + [pdf_text]:
                    if res is None or (useless and by != "pdf"):
                        r2 = _deterministic(f.nse_subject, extra if useless else f"{text} {extra}")
                        if r2 and (res is None or r2[0] != "unclassified" or r2[1] == "compliance"):
                            res, (cat, sub, by) = r2, (r2[0], r2[1], "pdf")
                    if res and cat in NEEDS_SUBTYPE and (sub is None or useless):
                        # A useless one-liner can't be trusted for the subtype; the PDF wins.
                        s2 = subtype_for(cat, extra if useless else f"{text} {extra}")
                        if s2:
                            sub, by = s2, "pdf"
                    if res and (sub or cat not in NEEDS_SUBTYPE):
                        break
                amounts = amounts or amt.inr_crore_amounts(pdf_text)
                fx = fx or amt.fx_mentions(pdf_text)

        if useless:
            better = pdf_sub if pdf_sub and not is_useless(pdf_sub, f.nse_subject) else pdftext.gist_line(pdf_text)
            if better:
                title = better[:1].upper() + better[1:]
        if sub == "order_win":
            # A published size band beats stray small numbers scraped from a letterhead.
            band = amt.band_amount(title + " " + (pdf_sub or ""), pdf_text)
            if band and (not amounts or max(amounts) < band[0]):
                amounts, amount_note = [band[0]], band[1]
        primary = None
        if cat == "management_change":
            _, primary = mgmt_detail(f"{text} {pdf_sub or ''} {pdf_text or ''}" if not useless
                                     else f"{pdf_sub or ''} {pdf_text or ''}")

        cf = ClassifiedFiling(
            **f.model_dump(),
            category=cat or "unclassified",
            subtype=sub if res else "uncertain",
            classified_by=by or "default",  # type: ignore[arg-type]
            title=title,
            pdf_subject=pdf_sub,
            amounts_cr=sorted(set(amounts), reverse=True)[:5],
            amount_note=amount_note,
            fx_mentions=fx,
            people=people_in(f"{f.text} {pdf_sub or ''} {(pdf_text or '')[:1500] if cat == 'management_change' else ''}"),
            primary_person=primary,
        )
        out.append(cf)
        if res is None:
            leftovers.append((len(out) - 1, f"{f.nse_subject} | {f.text} | {pdf_sub or ''} | {(pdf_text or '')[:600]}"))

    llm_status = "not needed"
    batches = 0
    if leftovers:
        if not use_llm:
            llm_status = "disabled"
        elif not llm.available():
            llm_status = "no API key: leftovers kept as material/uncertain"
        else:
            llm_status = "ok"
            for i in range(0, len(leftovers), BATCH):
                chunk = leftovers[i:i + BATCH]
                user = "\n".join(f"id={out[idx].id} :: {txt[:900]}" for idx, txt in chunk)
                try:
                    items = llm.json_call(tier="lite", purpose="classify_leftovers", prompt_version=PROMPT_VERSION,
                                          system=_SYSTEM, user=user, schema=_SCHEMA,
                                          result_type=list[_LLMItem], unwrap="items")
                    batches += 1
                except llm.LLMUnavailable as e:
                    llm_status = f"failed, leftovers kept as material/uncertain ({str(e)[:120]})"
                    break
                by_id = {it.id: it for it in items}
                for idx, _ in chunk:
                    it = by_id.get(out[idx].id)
                    if it and it.category in CATEGORIES:
                        cat = it.category if it.material else "unclassified"
                        out[idx] = out[idx].model_copy(update={
                            "category": cat, "subtype": it.subtype or (None if it.material else "routine"),
                            "classified_by": "llm"})

    steps = Counter(c.classified_by for c in out)
    return out, ClassifyStats(by_step=dict(steps), pdfs_read=pdfs_read,
                              llm_filings=sum(1 for c in out if c.classified_by == "llm"),
                              llm_batches=batches, llm_status=llm_status)
