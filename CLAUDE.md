# FINN: conventions

A mobile-first investor agent built for the ENAM Holdings AI Engineer challenge. The brief and glossary are in `../ENAM_AI_Engineer_Build_Challenge_Main.pdf` and `../ENAM_Build_Challenge_Reference.pdf`. ENAM grades product judgment and architecture, not completeness: **thin but working beats broad but broken**, and a demo that doesn't run is the worst outcome.

## Point of view (every feature must serve this)
Investors own **theses**, not filings. FINN reads everything but only interrupts when something **confirms, threatens or tests** why you own a stock, and it holds management to what they promised.
- The signature feature is the **results card in the push feed with a "guided X last call → delivered Y" row**. Push and pull share one promise store, so they are not two separate apps.
- If a feature doesn't serve the thesis or the promise, cut it.

## Repo layout
```
frontend/   React 18 + Vite + TypeScript + Tailwind (mobile-first PWA)
backend/    Python FastAPI + ingestion jobs (finn/ package, scripts/)
data/
  snapshots/  committed JSON the frontend reads (brief.json, research/<SYMBOL>.json)
  raw/        gitignored: raw HTTP responses / PDFs, disk cache
  llm_cache/  gitignored: LLM responses keyed by content hash
```

## Hard rules
1. **The LLM never does arithmetic.** Models only *extract* (numbers as written, plus units and period) or *write prose*. Every growth rate, margin, QoQ/YoY figure, ratio, threshold check and promise grade (exceeded / met / missed / revised / pending) is computed in Python. When prose needs a number, Python computes it and passes it into the prompt as given text.
2. **The pull side uses primary sources only**: annual reports, concall transcripts, exchange filings and screener's standardised tables. No news, broker notes or research reports, and nothing from the model's own knowledge. Prompts must say "answer only from the provided excerpts; say 'not disclosed' otherwise".
3. **Every claim has a source.** Each extracted fact carries `{doc_id, page|section, quote}`. The UI shows a source tag on every claim. A number in generated text that isn't found in its cited excerpt gets flagged, not shown as fact.
4. **The demo must never break.** The frontend reads precomputed static snapshots first and calls the live backend only for a ticker it has never seen, showing a progress state. Every data source must fail soft (empty and flagged, never a crash). If a window is empty, widen it and say so.
5. **No LinkedIn scraping** (against its terms). Management bios come from the Reg 30 annexure in the filing itself, plus a LinkedIn *search* link.
6. **Secrets live only in `backend/.env`**, never in the frontend bundle or in git.

## Model routing (free tiers)
| Step | Model |
|---|---|
| Classify filings, rank | **Rules / regex first**; the LLM only gets what rules can't place |
| Bulk extraction (leftover classification, results tables if XBRL fails, bios, guidance statements from transcripts) | `gemini-flash-lite` |
| Reasoning (qualitative promise judgments, bull/bear, concall questions, Morning Call script) | `gemini-flash` |
| Fallback when Gemini rate-limits or errors | Groq |

- Use structured JSON output with a schema for every extraction call, and validate it with Pydantic.
- Cache key: `sha256(model + prompt_version + input)` stored in `data/llm_cache/`. Never call the same thing twice.
- Log every call (model, tokens in/out, cached?, est. cost). Research output carries a cost line ("6 calls, 4 cached, $0.00x").
- Chunk documents before they reach a model. Send relevant sections (MD&A, guidance Q&A), never whole PDFs.

## Data sources
- **NSE corporate announcements**: JSON API. NSE reportedly blocks cloud IPs, so push ingestion runs **locally or in GitHub Actions** and writes `data/snapshots/brief.json`. The server never calls NSE at request time.
  - Verified 15 Nov 2026 from a residential IP: the homepage returns **403** (bot wall), but `/api/*` returns 200 *without* cookies when sent a browser UA. Send `Accept-Encoding: gzip, deflate`, **not `br`**, because `requests` fails to decode NSE's brotli. Keep the warmup as best-effort and don't depend on it.
  - `corporate-announcements?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY` returns **every** NSE filing in the window (~700/day, ~1MB/day). One call per window, then filter to the portfolio. Adding `&symbol=X` narrows it. Without a date range it returns the full history since 2004 (1–2MB), so always pass dates.
  - `corporates-financial-results?index=equities&symbol=X&period=Quarterly` lists filings with an `xbrl` URL (nsearchives, fetchable). The list is **not sorted**, so sort by parsed `toDate` and filter `consolidated`.
  - `event-calendar?index=equities` gives upcoming board meetings and results dates, which feed the "reports this week" prep.
  - `quote-equity` → 403 and the BSE announcements API → 403. Don't use them.
- **Results numbers**: prefer NSE XBRL over LLM-parsing PDFs. For banks/NBFCs, EBITDA is n/a, so use NII / PPOP / PBT / PAT.
- **EOD price/volume**: yfinance (`SYMBOL.NS`), 1y daily for the portfolio plus `^NSEI`.
- **Fundamentals**: screener.in company page (tables plus links to annual reports and transcripts). PDFs are often BSE-hosted.
- **Polite fetching**: a real browser UA, roughly 1 req/s per host, retry with backoff, and a disk cache in `data/raw/` keyed by URL plus date.
- Announcement categories follow the reference PDF's taxonomy (results, M&A, fund_raise, management_change, litigation, insider_trading, earnings_call, investor_meetings, credit_rating, dividend, bonus_split, capex, press_release, postal_ballot, agm_egm, pledging, unclassified).

## Portfolio
- Holdings (18): RELIANCE, HDFCBANK, BAJFINANCE, INFY, PERSISTENT, TRENT, TITAN, DMART, DIXON, KAYNES, POLYCAB, LT, HAL, SUNPHARMA, PIDILITIND, TATAPOWER, ETERNAL, CDSL
- Ad-hoc research demo: KPITTECH, ASTRAL, DEEPAKNTR, CAMS
- The research engine must generalise to **any** NSE ticker. Never hard-code demo symbols into logic, only into config.

## Code conventions
- **Backend**: Python 3.11+, FastAPI, `httpx`, Pydantic v2 models for every snapshot shape (they are the API contract), type hints throughout. Money stays in ₹ crore as `float`, and percentages are stored as fractions (0.142), formatted only at the edge. Periods use `FY27Q2`-style keys.
- **Frontend**: TypeScript strict. Types mirror the Pydantic models. Design mobile-first at 375px, with desktop as a centred column.
- **Design language**: a crisp Swiss / ledger look. Pure white, black ink, one signal colour, a grotesk sans, tabular mono numerals, big numbers. No cream-paper newspaper, no dark neon dashboard, no tour modals. The app opens directly on the answer.
- Keep it small: three screens (Today, Holdings, Research). Don't add tabs.
- Don't commit or push unless asked.

## Scope cut line
Must-have: data plumbing → classification/ranking → results and management cards → Today screen → alerts and thresholds → research with the Promise Ledger → deploy and writeup. Differentiator to protect: thesis per holding plus the guided→delivered row. Optional: Morning Call (voice), Telegram push.

Deliberately out: login and multi-user, intraday data, network / "who to call", 3D views and sound, multiple themes, LinkedIn scraping.
