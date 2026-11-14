"""Categories, NSE-subject map, ordered regex rules, subtype rules.

Ladder (see classifier.py): NSE subject -> ordered regex on text -> PDF page 1 ->
flash-lite for leftovers. Everything here is deterministic and explainable.
Rules run on the filing's text only, never on the NSE subject string (the
subject "Analysts/Institutional Investor Meet/Con. Call Updates" contains
"Con. Call" and would make every meeting look like an earnings call).
"""
from __future__ import annotations

import re
from typing import Literal

Category = Literal[
    "results", "M&A", "fund_raise", "management_change", "litigation", "insider_trading",
    "earnings_call", "investor_meetings", "credit_rating", "dividend", "bonus_split", "capex",
    "press_release", "postal_ballot", "agm_egm", "pledging", "unclassified",
]
CATEGORIES: tuple[str, ...] = Category.__args__  # type: ignore[attr-defined]

# Subjects that carry no information: classification must come from text / PDF / LLM.
GENERIC_SUBJECTS = {
    "general updates", "updates", "disclosure under regulation 30", "others", "other",
    "committee meeting updates", "outcome of board meeting",
}

# Categories whose ranking depends on a subtype that the one-line text often lacks.
NEEDS_SUBTYPE = {"management_change", "credit_rating"}

# ---------------------------------------------------------------- level 1
# NSE subject (lowercased, exact) -> category. Subtypes come from rules below.
SUBJECT_MAP: dict[str, str] = {
    "analysts/institutional investor meet/con. call updates": "investor_meetings",
    "copy of newspaper publication": "unclassified",
    "press release": "press_release",
    "allotment of securities": "fund_raise",
    "esop/esos/esps": "unclassified",
    "change in management": "management_change",
    "change in director(s)": "management_change",
    "appointment": "management_change",
    "retirement": "management_change",
    "resignation": "management_change",
    "resignation of director/kmp/smp": "management_change",
    "cessation": "management_change",
    "investor presentation": "investor_meetings",
    "bagging/receiving of orders/contracts": "press_release",
    "shareholders meeting": "agm_egm",
    "postal ballot": "postal_ballot",
    "credit rating": "credit_rating",
    "credit rating- others": "credit_rating",
    "corporate insolvency resolution process": "litigation",
    "issue of securities": "fund_raise",
    "monitoring agency report": "unclassified",
    "news verification": "press_release",
    "rumour verification - regulation 30(11)": "press_release",
    "acquisition": "M&A",
    "amalgamation / merger": "M&A",
    "scheme of arrangement": "M&A",
    "sale or disposal": "M&A",
    "agreements": "M&A",
    "other restructuring": "M&A",
    "capacity addition": "capex",
    "statement of deviation(s) or variation(s) under reg. 32": "unclassified",
    "change in auditors": "unclassified",
    "certificate under sebi (depositories and participants) regulations, 2018": "unclassified",
    "action(s) initiated or orders passed": "litigation",
    "record date": "dividend",
    "dividend": "dividend",
    "bonus": "bonus_split",
    "split": "bonus_split",
    "buyback": "fund_raise",
    "insider trading": "insider_trading",
    "disclosure under sebi takeover regulations": "insider_trading",
    "pledge": "pledging",
    "trading window": "unclassified",
    "loss of share certificates": "unclassified",
    "duplicate share certificate": "unclassified",
    "compliance certificate": "unclassified",
}

# ---------------------------------------------------------------- level 2
_R = lambda p: re.compile(p, re.I)  # noqa: E731

# Ordered: first match wins. Things that merely *mention* results (press release,
# newspaper ad, transcript, deck, board-meeting notice) come before the broad
# results-outcome rule; litigation comes before M&A and corporate actions because
# dispute letters mention everything.
RULES: list[tuple[str, str | None, re.Pattern[str]]] = [
    ("M&A", "subsidiary_ipo", _R(r"initial public offer|\bipo\b|commence(ment)? of trading|trading in equity shares of")),
    # results family
    ("results", "press_release", _R(r"(press|media) release.{0,120}(financial )?results")),
    ("results", "newspaper_ad", _R(r"(newspaper|news paper|clippings|publication).{0,100}financial results|financial results.{0,60}(newspaper|publication)")),
    ("unclassified", "newspaper_ad", _R(r"newspaper|news paper|clippings")),
    ("earnings_call", "transcript", _R(r"transcript")),
    ("earnings_call", "recording", _R(r"recording|audio|webcast")),
    ("investor_meetings", "deck", _R(r"investor\W{0,3}s?\W{0,3} presentation|analyst presentation|earnings presentation|^presentation\b")),
    ("results", "auditor_report", _R(r"report of auditors? on (the )?financial")),
    ("results", "results_date", _R(r"to announce .{0,30}results|(scheduled (on|to be held)|to consider and approve|intimation of (the )?board meeting).{0,160}results")),
    ("results", "outcome", _R(r"un-?audited\s*(\([^)]{0,60}\)\s*)?((standalone|consolidated)\s*(and\s*(standalone|consolidated)\s*)?)?financial results"
                              r"|financial results? (for|of) the (quarter|period|half|year)|submitted to the exchange,? (the )?financial results")),
    ("results", "shareholder_letter", _R(r"shareholders? letter")),
    ("earnings_call", "call_notice", _R(r"earnings call|conference call|con\.? ?call|schedule of (the )?(conference|earnings) call|call to discuss (the )?(financial )?results")),
    ("investor_meetings", "meeting", _R(r"schedule of (analyst|meet)|analyst ?/? ?institutional investor|investors?\W? (meet|conference|meeting)|non-deal road ?show"
                                        r"|analyst\(s\)/investor\(s\) meet|outcome of analyst|analyst call")),
    # A list of KMPs authorised to make disclosures is routine, not a "change".
    ("unclassified", "compliance", _R(r"determine materiality|regulation 30\s*\(5\)")),
    # management: an action word near a role, or NSE's own phrasing. Bare "Board of
    # Directors" must not match (it appears in almost every outcome letter).
    ("management_change", None, _R(r"(appoint|re-?appoint|resign|retire|cessation|ceas)\w*.{0,80}(director|chair|\bceo\b|\bcfo\b|officer|\bkmp\b|\bsmp\b|secretary|president|senior management)"
                                   r"|(director|chair\w*|\bceo\b|\bcfo\b|officer|\bkmp\b|\bsmp\b)\W.{0,60}(appoint|resign|retire|cessation)"
                                   r"|change in (senior )?management|change in director")),
    # money in / out
    ("fund_raise", "qip", _R(r"qualified institution\w* placement|\bqip\b")),
    ("fund_raise", "preferential", _R(r"preferential (issue|allotment)|\bpi 20\d\d\b")),
    # QIP pricing steps ("floor price for the Issue ... ICDR") without the word QIP
    ("fund_raise", "qip", _R(r"floor price.{0,120}(issue|icdr)|issue price.{0,80}qualified")),
("fund_raise", "rights", _R(r"rights issue")),
    ("fund_raise", "buyback", _R(r"buy-?back")),
    ("fund_raise", "debt_routine", _R(r"commercial paper|non[- ]convertible|\bncds?\b|debentures?")),
    ("unclassified", "esop", _R(r"\besops?\b|\besps\b|employee stock|grant of [\d,]+ options|allotment of [\d,]+ shares\.?$")),
    # litigation
    ("litigation", "regulatory", _R(r"show[- ]cause|penalt(y|ies)|\bfined?\b|warning letter|import alert|form 483|\busfda\b.{0,40}(observation|inspection)"
                                    r"|\b(sebi|rbi|cci|cdsco|dcgi|government|ministry|regulator|india)\b.{0,15}\b(order|orders|ordered|directs?)\b"
                                    r"|\borders? \w+(?: \w+)? to (halt|stop|cease|suspend|recall|pay|shut)|search (and|&) seizure|\binvestigation\b|quality lapses")),
    ("litigation", "insolvency", _R(r"insolvency|\bcirp\b|\bnclat\b|\bnclt\b.{0,60}(insolvency|petition|section [79])")),
    ("litigation", "dispute", _R(r"litigation|lawsuit|arbitra(l|tion)|\bcourt\b|tribunal|tax demand|\bdispute")),
    # M&A / capex
    ("M&A", "wos_incorporation", _R(r"incorporat\w+ (of )?(a |its )?(wholly[- ]owned )?subsidiary|incorporated .{0,60}wholly[- ]owned subsidiary")),
    ("M&A", "jv", _R(r"joint venture")),
    ("M&A", "divestment", _R(r"divest|sale of (stake|business|shares)|disposal|business transfer")),
    ("M&A", "stake_change", _R(r"transfer of .{0,30}stake|stake (sale|transfer)")),
    ("M&A", "acquisition", _R(r"acqui(re|sition)|share purchase agreement|binding term sheet|merger|amalgamat|demerg|restructur")),
    ("capex", None, _R(r"capacity (addition|expansion)|commission(s|ed|ing)? .{0,40}(mw|plant|project)|expansion of .{0,40}(cent(er|re)|plant|facility)"
                       r"|new (plant|facility)|greenfield|brownfield|pumped storage")),
    # corporate actions
    ("dividend", None, _R(r"dividend|record date")),
    ("bonus_split", None, _R(r"bonus issue|sub-?division|stock split|split of")),
    ("pledging", None, _R(r"\bpledg|encumbrance")),
    ("insider_trading", None, _R(r"regulation 7\(2\)|insider trading|\bsast\b|regulation 29|acquisition of shares by promoter")),
    # press-release-ish
    ("press_release", "clarification", _R(r"clarification|news item|rumou?r|media statement")),
    ("press_release", "order_win", _R(r"bagging|\bbags\b|wins? orders?|order (win|inflow)|receiv\w+ .{0,20}orders?|contract (award|win)|letter of (award|intent)")),
    ("press_release", "approval", _R(r"\bapprov(al|ed)\b.{0,60}(usfda|fda|u\.s\.|ema|dcgi)|launch")),
    ("press_release", "award", _R(r"\bawards?\b|\bprize\b|certification|recogni[sz]")),
    ("press_release", "store_opening", _R(r"store opening")),
    ("press_release", "partnership", _R(r"collaborat|partnership|\bmou\b|strategic")),
    # routine paperwork
    ("agm_egm", None, _R(r"general meeting|\bagm\b|\begm\b|voting results|scrutini[sz]er")),
    ("postal_ballot", None, _R(r"postal ballot")),
    ("unclassified", "compliance", _R(r"dematerial|\biepf\b|investor education|re-?lodgement|registrar|share transfer agent|\brta\b|certificate (pursuant|under)"
                                      r"|regulation 74|statutory auditor|change in auditor|monitoring agency|deviation|trading window|duplicate share|loss of share"
                                      r"|redemption|repayment of|guarantee")),
]

# When the subject already fixed a category, a rule may only refine it (same category)
# or move it along one of these edges. Prevents e.g. a director's name in a press
# release turning it into a management change.
OVERRIDES: set[tuple[str, str]] = {
    ("investor_meetings", "earnings_call"),
    ("investor_meetings", "results"),          # results-date notice under the meet subject
    ("unclassified", "results"),               # newspaper ad of results
    ("unclassified", "fund_raise"),
    ("press_release", "results"),              # results press release
    ("press_release", "litigation"),           # news verification about an order
    ("press_release", "M&A"),
    ("fund_raise", "unclassified"),            # ESOP allotment under "Allotment of Securities"
    ("agm_egm", "postal_ballot"),
    ("M&A", "litigation"),
}

# Categories where the score needs a rupee amount; read the PDF if the text has none.
NEEDS_AMOUNT = {"M&A", "capex"}
NEEDS_AMOUNT_SUBTYPES = {"order_win", "qip", "preferential", "rights", "buyback"}

# ---------------------------------------------------------------- subtypes
_EXIT = re.compile(r"resign\w*|retire\w*|retirement|cessation|ceas\w+|step\w* down|demise|completion of (his|her) (term|tenure)", re.I)
_REAPPOINT = re.compile(r"re-?appoint\w*|re-?designat\w*|continuation", re.I)
_APPOINT = re.compile(r"appoint\w*|induct\w*|elevat\w*|designat\w*|joining", re.I)
_ROLES: list[tuple[str, re.Pattern[str]]] = [
    ("vice_chairman", re.compile(r"vice[- ]?chair(man|person)?", re.I)),
    ("chairman", re.compile(r"\bchair(man|person)?\b", re.I)),
    ("executive_director", re.compile(r"deputy managing director|deputy md\b|joint managing director", re.I)),
    # "CEO (Helicopter Complex)" is a business-unit head, not the CEO
    ("senior_mgmt", re.compile(r"\bceo\s*\(|chief executive officer\s*\(", re.I)),
    ("ceo_md", re.compile(r"managing director|\bm\.?d\.?\b|chief executive officer(?!\s*\()|\bceo\b(?!\s*\()|whole[- ]?time director", re.I)),
    ("cfo", re.compile(r"chief financial|\bcfo\b", re.I)),
    ("independent_director", re.compile(r"(?<!non-)(?<!non )(?<!non- )\bindependent director", re.I)),
    ("nonexec_director", re.compile(r"non[- ]{0,2}executive", re.I)),
    ("executive_director", re.compile(r"executive director", re.I)),
    ("senior_mgmt", re.compile(r"senior management|\bsmp\b|\bkmp\b|key managerial|company secretary|compliance officer|vice president|president|chief \w+ officer|\bhead\b", re.I)),
    ("director", re.compile(r"\bdirector", re.I)),
]

# (role, action) rank used to pick the dominant subtype when a filing names several people.
MGMT_RANK = ["chairman_exit", "ceo_md_exit", "cfo_exit", "vice_chairman_exit", "ceo_md_appoint",
             "cfo_appoint", "chairman_appoint", "executive_director_exit", "executive_director_appoint",
             "senior_mgmt_exit", "director_exit", "nonexec_director_exit", "senior_mgmt_appoint",
             "vice_chairman_appoint", "director_appoint", "nonexec_director_appoint",
             "independent_director_exit", "independent_director_appoint", "reappointment"]

_ABBR = re.compile(r"\b(Mr|Ms|Mrs|Dr|Shri|Smt|No|Sr|Jr|Ltd|Co|Pvt|viz)\.", re.I)
_AS_ROLE = re.compile(r"\bas\s+(?:the\s+|an?\s+)?(.{3,70}?)(?:\s+(?:of|w\.?e\.?f|with effect|for|effective|subject|on)\b|[,;]|$)", re.I)
# Bios / annexures (usually page 2) talk about past roles; stop reading there.
_BIO_CUT = re.compile(r"brief profile|annexure|other (required )?details|details (as )?required|disclosure of relationship|profile of", re.I)


def _role(fragment: str) -> str | None:
    for role, pat in _ROLES:
        if pat.search(fragment):
            return role
    return None


def mgmt_subtype(text: str) -> str | None:
    return mgmt_detail(text)[0]


def mgmt_detail(text: str) -> tuple[str | None, str | None]:
    """(dominant subtype, person named in that sentence), sentence by sentence.
    The role is read from the 'as <role>' phrase when present, so 'appointed Shri X,
    CEO (Helicopter Complex), as Additional Director designated as Director
    (Operations)' is a director appointment, not a CEO change. Tables with no
    sentence structure fall back to 'senior management' if the letter says so."""
    cut = _BIO_CUT.search(text)
    raw = text[: cut.start()] if cut else text
    senior_doc = bool(re.search(r"senior management", raw, re.I))
    text = _ABBR.sub(lambda m: m.group(1), raw)  # 'Mr.' must not end a sentence
    found: list[tuple[str, str]] = []
    # Split on sentence ends, not on initials ("Noel N. Tata"): need 2 lowercase/digits before the dot.
    for sent in re.split(r"(?<=[a-z0-9)]{2}[.;])\s+|\n\s*\n", text):
        if _REAPPOINT.search(sent) and not _EXIT.search(sent):
            found.append(("reappointment", sent))
            continue
        action = "exit" if _EXIT.search(sent) else ("appoint" if _APPOINT.search(sent) else None)
        if not action:
            continue
        m = _AS_ROLE.search(sent)
        role = (_role(m.group(1)) if m else None) or _role(sent) or ("senior_mgmt" if senior_doc else None)
        if role:
            found.append((f"{role}_{action}", sent))
    known = [(s, sent) for s, sent in found if s in MGMT_RANK]
    if not known:
        return None, None
    best, sent = min(known, key=lambda x: MGMT_RANK.index(x[0]))
    names = people_in(sent)
    return best, (names[0] if names else None)


_CREDIT: list[tuple[str, re.Pattern[str]]] = [
    ("downgrade", re.compile(r"downgrad", re.I)),
    ("outlook_negative", re.compile(r"outlook.{0,40}negative|negative outlook|watch with negative", re.I)),
    ("upgrade", re.compile(r"upgrad", re.I)),
    ("outlook_positive", re.compile(r"outlook.{0,40}positive|positive outlook", re.I)),
    ("withdrawal", re.compile(r"withdraw", re.I)),
    ("reaffirm", re.compile(r"re-?affirm|retain|maintain|reiterat", re.I)),
    ("assigned", re.compile(r"assign", re.I)),
]


def credit_subtype(text: str) -> str | None:
    for name, pat in _CREDIT:
        if pat.search(text):
            return name
    return None


def subtype_for(category: str, text: str) -> str | None:
    if category == "management_change":
        return mgmt_subtype(text)
    if category == "credit_rating":
        return credit_subtype(text)
    return None


# Names after Mr/Ms/Mrs/Dr/Shri/Smt for management cards (a hint, not a bio).
PERSON_RE = re.compile(r"\b(?:Mr|Ms|Mrs|Dr|Shri|Smt)\.?\s+((?:[A-Z][a-zA-Z]*\.?\s?){1,5})")
# names may follow a title without the period once _ABBR has stripped it
_NAME_STOP = re.compile(r"\s+(CEO|CFO|MD|Chief|Company|Managing|Director|Executive|Independent|Non|Whole|Vice|Chairman|"
                        r"Chairperson|Additional|Senior|President|Officer|Group|Limited|Ltd|Head|Deputy|DIN|Retirement|Appointment)\b.*$")


def people_in(text: str) -> list[str]:
    seen: list[str] = []
    for m in PERSON_RE.finditer(text):
        name = _NAME_STOP.sub("", re.sub(r"\s+", " ", m.group(1))).strip(" .")
        key = re.sub(r"[.\s]", "", name).lower()
        if len(name) <= 3 or any(re.sub(r"[.\s]", "", s).lower() == key for s in seen):
            continue
        # "Tata" after "Noel N. Tata" is the same person (surname-only re-mention)
        if " " not in name and any(s.split()[-1].lower() == name.lower() for s in seen):
            continue
        seen.append(name)
    return seen[:6]
