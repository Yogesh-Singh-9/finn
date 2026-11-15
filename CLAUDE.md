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
Two tiers, each an ordered fallback chain set in `backend/.env` (swap models without code; `finn/llm.py`):
- `FINN_LLM_CHEAP=gemini-3.5-flash-lite,groq:llama-3.3-70b-versatile`: rules-leftover classification, mgmt-card extraction, one-line summaries, transcript guidance extraction.
- `FINN_LLM_STRONG=gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash,groq:llama-3.3-70b-versatile`: qualitative promise judgments, bull/bear, concall questions, Morning Call script.
- The 2.5 models return 404 for new keys. The strong models often time out or 503 ("high demand"), which is why the chain exists.
- Each link gets `FINN_LLM_TIMEOUT_S` (45s) plus one backoff retry on 429/5xx. A link that fails twice is skipped for the rest of the process (circuit breaker). Links without a key are skipped.
- Rules / regex always run first; the LLM only gets what rules can't place.
- **Honest cache:** the key is `sha256(provider:model | prompt_version | system | user)` of the model that *actually answered*. A link's cache is read only after every link above it has failed live (or is breaker-open), so a fallback answer is never served as the primary's. Failed or invalid answers are never written. Bump `PROMPT_VERSION` when a prompt changes.
- Use structured JSON output with a schema for every extraction call, and validate it with Pydantic.
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
  - `event-calendar?index=equities` gives upcoming board meetings and results dates. It's empty for the portfolio right now (everyone has reported Q2), so **there's no "reports this week" prep screen**. Each holding gets a **"watch next quarter" line** built from its latest guidance instead.
  - `quote-equity` → 403 and the BSE announcements API → 403. Don't use them.
- **Results numbers: one source per number.**
  - **Screener's quarterly table** is the source for every quarterly actual, including the "delivered" side of the promise store, trajectory and history.
  - **NSE XBRL is used only for the latest results card** (the quarter just filed, which screener may not have picked up yet). Don't build two sources for the same number. Concretely: if screener already has the quarter, the card uses screener for all columns. XBRL fills the current column only when screener lags (e.g. KAYNES at FY27Q2). The two sources disagree slightly on PBT/EBITDA (TRENT FY27Q2 PBT: XBRL 640 vs screener 619), so never mix them inside a single comparison without labelling it.
  - **The card always names its source** ("screener" or "NSE XBRL"). When it's XBRL because screener hasn't caught up, it says so ("screener not updated yet; current quarter from NSE XBRL").
  - **PBT definition (checked against TRENT's FY27Q2 PDF):** the PDF's statutory "Profit before tax" (line 6, after the share of associates/JVs) is ₹619.06 cr, which **matches screener**. XBRL `ProfitBeforeTax` is ₹640.12 cr because the Ind AS taxonomy puts `ShareOfProfitLossOfAssociatesAndJointVentures…` (−21.06) *below* tax. So screener PBT = PDF PBT. In an XBRL fallback, label PBT "before share of associates/JVs" rather than silently adding the share back (presentation varies by company).
  - **Screener's "Operating Profit" (our EBITDA)** folds the associate/JV share into expenses (TRENT: 1,057 vs 1,078 = revenue − operating costs in the PDF). It's consistent across quarters, so QoQ/YoY stay apples to apples. Label it "EBITDA (screener)" and don't compare it with an XBRL-derived EBITDA.
  - For banks/NBFCs, EBITDA is n/a, so use NII / PPOP / PBT / PAT.
- **EOD price/volume**: yfinance (`SYMBOL.NS`), 1y daily for the portfolio plus `^NSEI`.
- **Fundamentals**: screener.in company page (tables plus links to annual reports and transcripts). PDFs are often BSE-hosted.
- **Polite fetching**: a real browser UA, roughly 1 req/s per host, retry with backoff, and a disk cache in `data/raw/` keyed by URL plus date.
- Announcement categories follow the reference PDF's taxonomy (results, M&A, fund_raise, management_change, litigation, insider_trading, earnings_call, investor_meetings, credit_rating, dividend, bonus_split, capex, press_release, postal_ballot, agm_egm, pledging, unclassified).

## Brief pipeline (phase 2): classify → fold → score
`backend/scripts/build_brief.py` turns `data/processed/` into `data/snapshots/brief.json`. Code is in `finn/classify/` and `finn/brief/`.
- **Classification ladder** (`classify/classifier.py`, rules in `classify/taxonomy.py`):
  1. The NSE subject maps to a category. Regex can refine it within the allowed `OVERRIDES` edges.
  2. Ordered regex runs on the text, with NSE's "X has informed the Exchange about" prefix removed. **Rules never see the subject string**: the analyst-meet subject contains "Con. Call".
  3. **Page 1–2 of the PDF** is read when the one-liner is useless ("General Updates", "change in Management", "Credit Rating"), when a needed subtype is missing (management role, rating direction), or when the score needs a ₹ amount. If the one-liner is useless, **the PDF result overrides it**.
  4. flash-lite gets the leftovers, ~20 per call.
  5. With no model, leftovers become `unclassified/uncertain` and get +25 (**unsure → material**).
- **Management subtypes:** read the role from the "as <role>" phrase, cut bios/annexures, and don't split sentences on "Mr." or on initials. A business-unit "CEO (Helicopter Complex)" is senior management. A Deputy MD is an executive director. A list of KMPs "authorised to determine materiality" is routine compliance, not a change.
- **Order-size bands** (L&T style "(Major*)" plus a "Value in ₹ Cr" footnote table) → the band's lower bound, labelled as such.
- **Folding** (`brief/events.py`):
  - duplicates: same text in the same minute, or the same attachment (byte size) filed twice the same day under two subjects; keep the better-classified copy (management filings are exempt, the minute fold handles them)
  - **results day:** the outcome plus same-day deck, results press release, call notice and dividend, on symbol + date
  - **management:** same symbol + same minute; the dominant subtype goes by `MGMT_RANK`
  - **storylines:** same symbol + category + subtype within 7 days (QIP, IPO, NCLAT case); untyped pieces join the nearest chain
- **Score** (`brief/score.py`). No model ever produces importance. The parts are:
  - category base
  - subtype adjustment
  - uncertain +25
  - ₹ amount ÷ TTM revenue (screener)
  - **numbers** (results only): YoY revenue/NII and PAT growth vs the company's own last 8 YoY growths, in σ (σ floored at 5pp). ≥3σ +25, ≥2σ +18, ≥1.5σ +10, ≥1σ +5. A PAT fall of ≥20% gets at least +18, and a swing to a loss gets +25.
  - **guidance** (results only): from the promise ledger. A miss vs the last call's guidance is +15; a full-year target behind is +8
  - next-session reaction: abnormal return vs NIFTY in σ of 60 sessions, plus volume vs the 20-day average
  - thesis: challenges a driver +15, supports +5 (`holdings/thesis.py`, set after summaries; score and tier recomputed)
- **Score rules:**
  - **One price move, one owner:** when events share a reaction session, results own it, otherwise the most material event does, and the others carry a note.
  - The reaction isn't credited to routine paperwork.
  - Tiers: needs_you ≥ 60, glance ≥ 20, set_aside below that. The displayed score caps at 100; ranking uses the uncapped sum.
- `brief.json` carries every event's `breakdown` and a `ladder` block (counts per step, PDFs read, LLM calls, **filings that never touched a model**).
- Regression case: TRENT 29 Oct 16:32. Four filings → one event "Chairman exit: Noel N Tata · +3 more board changes". The chairman retirement outranks the two director appointments, and the results own the session reaction.

## Cards (phase 3): `finn/cards/`
- **Results card** (`results.py`), on every results event:
  - Revenue, EBITDA (screener), PBT and PAT, each with QoQ/YoY, plus margins in bps. All arithmetic is in Python.
  - Screener when it has the quarter. NSE XBRL fills the current column only when screener lags; then EBITDA/PBT carry no growth (definitions differ), and revenue/PAT growth is labelled "current from NSE XBRL, bases from screener".
  - Checks: sales − expenses = EBITDA; EBITDA + OI − interest − dep = PBT; PBT × (1 − tax%) = PAT (XBRL: income − expenses = PBT; PBT − tax + JV + discontinued = PAT). Any failure → status "unverified, open filing".
  - Banks/NBFCs: NII = interest earned − interest expended. PPOP comes only from the bank XBRL (current quarter, no base), else n/a with the reason. EBITDA is n/a.
  - The headline is built in code ("Revenue +10% YoY, EBITDA margin +446 bps, PAT +114%"). `guidance` holds the guided → delivered rows from the promise store (phase 6); the frontend hides the slot when it is empty.
- **One-offs** (`oneoffs.py`), on every results card:
  - Sources: the XBRL `ExceptionalItemsBeforeTax` line, plus "excluding / non-operational / one-time … ₹X crore" passages in the outcome PDF, press release and deck.
  - Rules pick out the passages and flash-lite names each one-off (amount, gain/charge, quote). Python checks the quote and amount verbatim (OCR-tolerant) and drops duty, price and tax effects.
  - **The company's adjusted figure is found by rules:** a number on a PBT/PAT line of the company's own text that equals reported − one-off (tolerance: the tighter of 1.5% and 30% of the one-off).
  - When one is found, the headline and the numbers score use it, labelled "company figure", with the reported figure shown beside it. Otherwise the headline says "(incl. ₹X cr one-off gain/charge)".
  - Test case: TITAN Q2 FY27. The ₹671 cr Ind-AS credit; adjusted PAT 1,722 vs 1,120 is +54% (reported +114%).
- **Management card** (`mgmt.py`):
  - `short` is the phone headline, built in code. A succession reads "Chairman: Noel Tata → Bahram Vakil · +2 more"; otherwise it shows the most senior change.
  - Reads the filing PDF(s), ≤8 pages including the Reg 30 annexure/profiles. flash-lite extracts person, role, change, effective date, reason, background and prior roles.
  - Python keeps only what appears verbatim in the text. Missing employers go to `dropped`, and a paraphrased quote is replaced by the real sentence.
  - If the model finds nobody (a correction letter), the card says "no change of position" instead of guessing.
  - LinkedIn people-search link only.
- **Summaries** (`summaries.py`): one line, flash-lite, batched 20, for needs_you/glance events whose title is a raw cover-letter line. Results and management are excluded; management uses the card headline. A summary carrying a number not found in its source is rejected.
- Snapshots are written as UTF-8 **and** ASCII-escaped, so they load under any reader encoding. Every file read/write passes `encoding="utf-8"`, and scripts reconfigure stdout.

## Research + Promise Ledger (phase 6): `finn/research/`
- **One promise store** `data/processed/promises/<SYM>.json` (a `Ledger`), written by the research build and read by `build_brief.py` for the results card's guided → delivered rows. Build research first, then the brief.
- `transcripts.py`: the last 4 transcripts screener links (BSE PDFs), pages stored in `data/processed/transcripts/<SYM>.json`. The call date comes from the cover and the quarter discussed is the last quarter ending ≥10 days before it. Rules keep only management sentences with a forward cue or a metric + number + past tense, tagged `[p.N]`.
- `ledger.py`:
  - flash-lite extracts guidance plus company-stated actuals (segments, cc growth, EBIT margin, store adds) with quote and page. A quote not found on its page (or the next page's start) is **dropped**. Horizon words must appear on that page.
  - Python parses targets (ranges, at least / at most / around, "double digit" → ≥10%) and resolves horizons relative to the call date (FY / quarter / half / multi-year / unspecified).
  - **Units:** INR billion/million → crore. "1.5x industry" can't be graded. A target whose unit doesn't match the metric's natural unit isn't graded. The `other` metric key never matches actuals.
  - **Grading:** company-level metrics are graded against screener; segment / cc / EBIT targets against a later call's stated figure. PAT uses the results card's company-adjusted figure (one-offs). Ongoing targets are graded on the latest quarter after the call; full-year targets show H1 progress (on track / behind); multi-year % targets are tracked against the latest quarter.
  - **Revisions:** same metric + segment + horizon + unit on a later call → the older promise is `revised` (with direction), or `reiterated` when unchanged.
  - **Qualitative:** strong chain, ≤8 per dossier, evidence passages from later calls picked by Python. A met/missed whose evidence quote isn't found verbatim becomes `not_comparable`.
  - `card_rows()` is the push side: the latest pre-results guidance per metric vs the quarter just reported (≤3 rows). The `guidance` score part: missed +15, behind +8.
- `financials.py`: vitals and sections from screener annual tables. Thresholds are constants at the top (growth <8%, margin >200 bps below 5y avg, CFO/PAT <0.7, ROE <12%, D/E >1). Chips are coloured (signal) only when off. Every section has a code-built one-line conclusion.
- `business.py` (business snapshot, first fold):
  - Rules pick ≤2 overview pages (front 40) and ≤3 segment-note pages, scored by numbers plus a segment-table heading. flash-lite extracts the description (with quote) and each segment's revenue as printed.
  - Python checks each number and quote on its page, reads the unit from the page (pypdf renders ₹ as "`" or "H"), converts to crore, computes the mix and checks the total against screener's revenue for that FY.
  - All 5 demos tie to screener within 0.3%.
- `dossier.py`: verdict strip (code), AR MD&A risk/outlook pages (≤3), bull/bear/3 questions (one strong call; points cite item ids F/L/E only; an uncited point is dropped; a number not found in its cites is `flagged`). Questions come from Python-ranked divergences; without a model they fall back to templates. Writes `data/snapshots/research/<SYM>.json` + `index.json`, with a cost line (calls, cached, tokens, $).
- `scripts/build_research.py SYM ... [--refresh]` precomputes. `finn/api.py` (`uvicorn finn.api:app --app-dir backend --port 8000`) builds a never-seen ticker live in a thread with stages; the frontend reads the static snapshot first and Vite proxies `/api`.

## Holdings + thesis (phases 5+7): `finn/holdings/`
- `scripts/build_holdings.py` seeds a thesis per holding (`data/processed/theses.json`): flash-lite reads the latest 2 calls and names 3 drivers, each with a quote verified on its page (else dropped). The "watch next quarter" line is the ledger's latest pending numeric target when there is one, else the model's pick, accepted only from the latest call. It writes `data/snapshots/holdings.json` (250 sessions of closes/volumes, thesis, research flag, 30-day category counts).
- Event tagging (`thesis.tag`, prompt `thesis-tag-v2`) is strict: only results can be evidence for a metric driver, and a deal or press release touches a driver only if it is about that exact business. Tags are made against the **seeded** drivers. User edits live in localStorage (`finn-thesis-SYM`) and the UI says so.
- Alert settings (price ±X%, volume N× its 20-day average, categories) are stored in localStorage (`finn-alerts`). The alerts/week estimate is back-tested client-side on the price history, plus filings at the 30-day rate. The same thresholds (`frontend/src/alerts.ts` `trips()`) filter Today's unusual-moves strip. `brief.json` carries candidate moves for the last 22 sessions at the sliders' loosest setting (±1% or 1.5× volume, or |z| ≥ 2).
- **Full rebuild order:** build_research → build_holdings → build_brief → build_holdings (refreshes category counts) → build_morning → `node frontend/scripts/sync-data.mjs`.

## Morning Call (optional): `finn/brief/morning.py`
- `scripts/build_morning.py` writes `data/snapshots/morning.json` once per brief: the last 7 days' needs_you events (≤5) by score. Python writes each item's facts in spoken form (`spoken()`: up/down, basis points, crore rupees; digits unchanged). The strong chain (then cheap) only turns the facts into prose (`morning-v3`).
- Each sentence is checked: a digit not in that chapter's facts, or a spelled-out number, drops the sentence. A chapter with nothing left keeps the template line. With no model, a template from the same facts is used.
- Frontend (`components/MorningCall.tsx`): browser speechSynthesis, one utterance per sentence (highlighted), a chapter tap seeks there and opens the card (`openSignal.ts`), 1.25× restarts the current sentence. If the browser has no voice, the script is shown instead.

## Deploy
- **Frontend: Vercel, static.** Root is `frontend/`, and `vercel.json` runs `npm run build` (which first syncs `data/snapshots` into `public/data`). Precomputed dossiers and the brief never touch a backend.
- **Research API: Render free tier** (`render.yaml` blueprint, region singapore, `rootDir: backend`). Keys are set in the Render dashboard (`sync: false`), never in git. The Vercel env var `VITE_API_BASE=https://<service>.onrender.com` turns on live builds in the deployed UI. Without it the UI says "live build runs locally in this prototype".
- **Live builds must finish.**
  - A wall-clock budget per tier from the build's start (`FINN_LIVE_CHEAP_BUDGET_S` 150, `FINN_LIVE_STRONG_BUDGET_S` 210). After that, live calls are skipped, and the dossier ships without bull/bear and says so.
  - `_post` enforces `FINN_LLM_TIMEOUT_S` as a hard wall clock, because httpx's timeout is per read (a 175s flash-lite call was seen).
  - The 4 transcript extractions run in parallel.
  - The breaker resets per build.
  - Render's strong chain puts 3.6-flash first, because 3.8 often times out.
- The free service sleeps after ~15 min idle. The UI pings `/api/health` first and says the first request can take ~50s. One build runs at a time (others queue). Built dossiers live on Render's ephemeral disk until a restart.

## Running the data refresh
From `backend/`: `.venv/Scripts/python scripts/refresh.py` (all sources, ~1 min). Add `--only nse|prices|screener` or `--symbols A,B` for partial runs. A partial run merges into the existing files, and a source that fails keeps the last good data. Output goes to `data/processed/` (filings.json, prices.json, calendar.json, results/<SYM>.json, fundamentals/<SYM>.json, manifest.json with every source status and any redirect seen).

## Portfolio
- Holdings (18): RELIANCE, HDFCBANK, BAJFINANCE, INFY, PERSISTENT, TRENT, TITAN, DMART, DIXON, KAYNES, POLYCAB, LT, HAL, SUNPHARMA, PIDILITIND, TATAPOWER, ETERNAL, CDSL
- Ad-hoc research demo: KPITTECH, ASTRAL (precomputed, outside the portfolio), DEEPAKNTR, CAMS
- **Promise Ledger demo:** TITAN and POLYCAB (primary, both held), INFY third. TRENT has **no concall transcripts**, so it isn't a ledger or research demo. It's the **management-change card demo** (29 Oct 2026: chairman Noel Tata's retirement plus director changes, all filed at 16:32).
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
