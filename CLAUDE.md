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
  processed/  committed normalised source data (filings, prices, fundamentals), so snapshots rebuild offline
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
- **NSE corporate announcements**: JSON API. Push ingestion runs **locally** (the main path for now) and writes `data/snapshots/brief.json`. A scheduled GitHub Actions refresh is possible but deferred to phase 9. The server never calls NSE at request time.
  - Verified 15 Nov 2026 from a residential IP: the homepage returns **403** (bot wall), but `/api/*` returns 200 *without* cookies when sent a browser UA. Send `Accept-Encoding: gzip, deflate`, **not `br`**, because NSE's brotli doesn't decode reliably. Keep the warmup as best-effort and don't depend on it.
  - Verified from GitHub Actions runners (user-tested): announcements, financial results and event calendar all return 200, and the homepage is 403 there too. On runners, `www.nseindia.com/api` **redirects to `www.nseindia.in`**, and attachments come from `nsearchives.nseindia.in`. So **always follow redirects, never hardcode the `.com` host** (the base URL is config), and use attachment/XBRL URLs exactly as returned. Yahoo and screener work from runners too.
  - `corporate-announcements?index=equities&symbol=X&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY` returns one symbol's filings in the window. Without `symbol` it returns **every** NSE filing (~700/day, ~1MB/day), too heavy for 30 days, so fetch per symbol. Without a date range it returns the full history since 2004, so always pass dates.
  - **The brief snapshot covers the last 30 days** (Q2 results season). The frontend filters 24h / 7d / 30d from that one snapshot. Don't fetch per window.
  - On 15 Nov 2026 the **local** run was also redirected `www.nseindia.com → www.nseindia.in`. The redirect isn't runner-only.
  - **Results filings come from `integrated-filing-results?index=equities&symbol=X&type=Integrated%20Filing-%20Financials`** (SEBI Integrated Filing, Q3 FY25 onward). The legacy `corporates-financial-results` **stops at Dec 2024**, so don't use it. Rows carry `qe_Date`, `consolidated`, `broadcast_Date` and an `xbrl` URL. Sort by period end, and prefer consolidated.
  - **The XBRL holds only the current quarter (`OneD`) and year-to-date (`FourD`)**, with values in rupees (÷1e7 → crore). Tag prefixes vary (`in-capmkt:` now, `in-bse-fin:` legacy), so match on the local name. There are no prior-quarter columns, so QoQ/YoY bases come from screener. Banks use a different taxonomy (`InterestEarned`, `InterestExpended`, …).
  - Results day doesn't appear as a "results" subject in announcements. It shows up as `Outcome of Board Meeting` (often alongside `Investor Presentation` / `Press Release`), plus the integrated filing above. Classification should join them on symbol and date.
  - `event-calendar?index=equities` gives upcoming board meetings and results dates, which feed the "reports this week" prep.
  - `quote-equity` → 403 and the BSE announcements API → 403. Don't use them.
- **Results numbers: one source per number.**
  - **Screener's quarterly table** is the source for every quarterly actual, including the "delivered" side of the promise store, trajectory and history.
  - **NSE XBRL is used only for the latest results card** (the quarter just filed, which screener may not have picked up yet). Don't build two sources for the same number. Concretely: if screener already has the quarter, the card uses screener for all columns. XBRL fills the current column only when screener lags (e.g. KAYNES at FY27Q2). The two sources disagree slightly on PBT/EBITDA (TRENT FY27Q2 PBT: XBRL 640 vs screener 619), so never mix them inside a single comparison without labelling it.
  - For banks/NBFCs, EBITDA is n/a, so use NII / PPOP / PBT / PAT.
- **EOD price/volume**: yfinance (`SYMBOL.NS`), 1y daily for the portfolio plus `^NSEI`.
- **Fundamentals**: screener.in company page (tables plus links to annual reports and transcripts). PDFs are often BSE-hosted.
- **Polite fetching**: a real browser UA, roughly 1 req/s per host, retry with backoff, and a disk cache in `data/raw/` keyed by URL plus date.
- Announcement categories follow the reference PDF's taxonomy (results, M&A, fund_raise, management_change, litigation, insider_trading, earnings_call, investor_meetings, credit_rating, dividend, bonus_split, capex, press_release, postal_ballot, agm_egm, pledging, unclassified).

## Running the data refresh
From `backend/`: `.venv/Scripts/python scripts/refresh.py` (all sources, ~1 min). Add `--only nse|prices|screener` or `--symbols A,B` for partial runs. A partial run merges into the existing files, and a source that fails keeps the last good data. Output goes to `data/processed/` (filings.json, prices.json, calendar.json, results/<SYM>.json, fundamentals/<SYM>.json, manifest.json with every source status and any redirect seen).

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
