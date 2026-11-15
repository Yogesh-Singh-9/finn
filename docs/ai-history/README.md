# AI history

FINN was built with an AI assistant over **two conversations**, kept separate on purpose: one for deciding *what* to build, one for building it.

## 1. Planning and competitive analysis

The first chat turned the brief into a product point of view before any code was written:
- reading the challenge brief and the reference glossary,
- looking at how existing investor tools handle filings and alerts, and where they fall short,
- settling on **theses, not filings**, and on the signature feature: the results card's *"guided X last call → delivered Y"* row, backed by one promise store shared by push and pull,
- the scope cut line (three screens; what's in, optional, and deliberately out).

The output of this chat became the conventions file the build followed (`CLAUDE.md` in the repo root).

## 2. Build

The second chat is the build itself, done in phases. Each phase ended with a working app and a commit:

1. Data plumbing: NSE announcements and integrated-filing XBRL, screener tables, yfinance. Fail-soft fetching and a disk cache.
2. Classification ladder (subject → regex → PDF → model), event folding, and the explainable score.
3. Results and management cards: arithmetic checks, one-offs, Reg 30 bios, and an honest LLM fallback chain with caching.
4. The Today screen: a Swiss ledger-style PWA.
5. Research and the Promise Ledger: transcripts, grading in Python, revisions, a qualitative judge, bull/bear with citations, and guided → delivered rows on results cards.
6. Static Vercel deploy; the Holdings tab (theses, thesis-tagged scoring, alert back-test); the Morning Call.
7. Render research API for live builds; KPITTECH and ASTRAL dossiers; business snapshot from the annual report's segment note; alert thresholds driving the moves strip; polish.

Along the way, the human reviewed outputs line by line against the sources and pushed back where the AI was too generous or wrong, for example:
- "inching towards double digit" graded as met → now pending until the target is reached,
- a reported result extracted as guidance,
- an industry-relative target graded against the company's own numbers.

Those corrections are in the commit history.

## Files

- `FINN-AI-build-history.md`: the build conversation with a reading guide (`build.md` is the same export without the guide)
