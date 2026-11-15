**Subject:** AI Engineer build challenge: FINN, an investor agent built around theses and promises

Hi,

**What I built.** FINN is a mobile-first investor agent with three screens:
- **Today:** about 230 filings in 30 days, ranked down to the 17 that need you, each with an explainable score.
- **Holdings:** a thesis per stock, with events tagged as supporting or challenging it.
- **Research:** a dossier for any NSE ticker.

The core idea is a single **promise store**. Every forward-looking statement from the last four concalls is graded in Python against what was delivered. The same store feeds the results card ("guided 11% → delivered 11.1%"), so push and pull aren't two separate apps.

**Decisions and trade-offs.**
- Rules first: 231 of 233 filings never touched a model. The model never does arithmetic: it only extracts numbers as written, or writes prose from numbers Python computed.
- Every claim carries a source, and any quote not found verbatim on its page is dropped.
- Primary sources only, and one source per number (screener, plus NSE XBRL when screener lags).
- Static snapshots first, so the demo can't break. Live builds for any other ticker run on a free Render backend.
- Total model cost so far is about $0.11 at list prices (free tiers).

**Left out on purpose.** Login, intraday data, news and broker notes, LinkedIn scraping, and Telegram push.

**Next steps.** On live builds, the business snapshot and bull/bear can be skipped. I've traced this to a retired Groq fallback model, plus slow PDF parsing on the free tier. The fix is ready and listed in the README. After that: a scheduled refresh on GitHub Actions, real push for needs-you events, and OCR for scanned transcripts.

- Live app: https://finn-three-hazel.vercel.app
- Code: https://github.com/Yogesh-Singh-9/finn
- AI history: https://github.com/Yogesh-Singh-9/finn/tree/main/docs/ai-history

Happy to walk through any of it.

Yogesh
