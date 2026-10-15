# Market Sentiment Analyzer — project context

Read this first. It carries the decisions made before this repo existed.

## What this is

A 3-agent pipeline. Input a stock/crypto ticker, output a BUY/HOLD/SELL signal
backed by news and social sentiment. University project, built solo (a second
dev may join later for the frontend).

Goals, in order: (1) a working, demoable multi-agent system, (2) clean enough
architecture to defend in an interview, (3) CV value for quant/SWE applications.
It is NOT a real trading system and must never be presented as investment advice.

## Architecture

    ticker -> Agent 1 Collector -> CollectedData
                                       |
                                       v
                     Agent 2 Analyzer -> SentimentAnalysis
                                       |
                                       v
                     Agent 3 Reporter -> Report

| Agent | File | LLM? | Job |
|---|---|---|---|
| Collector | `app/agents/collector.py` | no | NewsAPI + Yahoo Finance RSS + Reddit (PRAW) + yfinance price, fetched concurrently, normalized, relevance-filtered |
| Analyzer | `app/agents/analyzer.py` | yes | Scores each item -1..+1, weighted aggregate, VADER fallback |
| Reporter | `app/agents/reporter.py` | yes | Writes the prose summary; signal computed in code |

Orchestration is `app/pipeline.py` — three sequential awaits, then a write to
the run store, nothing clever.

Two modules sit outside `app/agents/` because more than one agent (or the API)
needs them, and design rule 1 forbids cross-agent imports:

- `app/store.py` — every run is recorded to SQLite at decision time
- `app/evaluate.py` — scores those recorded signals against forward returns

## Non-negotiable design rules

1. **`app/schemas.py` is the interface.** Agents communicate ONLY through those
   Pydantic models. No agent imports another agent's internals. If you change a
   schema, update both sides of that boundary deliberately.
2. **The BUY/HOLD/SELL signal is computed in code**, in `_signal()` in
   `reporter.py` — never by the LLM. The LLM writes prose only. Same numbers
   must always produce the same signal, and it must stay unit-testable.
3. **No agent fails hard.** A dead source appends to `warnings` and the pipeline
   still returns. Confidence drops when evidence is thin or sources are missing.
4. **Weighted aggregation.** News = 1.0, Reddit = 0.3–0.7 by upvotes. One viral
   post must not be able to flip the signal.
5. **Batch LLM calls** (8 items per call), don't loop one call per item.
6. **Never commit `.env`.** Keys are read via `app/config.py` only.

## Stack

Python 3.11+, FastAPI, LangChain, Pydantic v2 schemas. The LLM is swappable
between Claude and OpenAI via `LLM_PROVIDER` in `.env`; see `app/llm.py`.
Frontend is React + Vite + Recharts in `frontend/`.

Run both:
    uvicorn app.main:app --reload     # :8000
    cd frontend && npm run dev        # :5173

Public demo on GitHub Pages: `.github/workflows/pages.yml` builds the
frontend with `npm run build:demo` (VITE_DEMO=true), which reads saved
Reports from `frontend/public/demo/` instead of calling the API, since Pages
cannot run the backend. Those files are real runs, recorded by
`python scripts/snapshot_demo.py`, which refuses to write anything if a run
fell back to VADER or the template summary.

## Endpoints

- `GET /analyze/{ticker}` — full pipeline, the real endpoint
- `GET /debug/collect/{ticker}` — Agent 1 only
- `GET /debug/analyze/{ticker}` — Agents 1+2
- `GET /health`
- `GET /history?ticker=&limit=` — past runs, newest first
- `GET /history/stats` — how much has been recorded
- `GET /history/evaluate?ticker=&horizon=&llm_only=` — forward-return scoring

The debug endpoints exist so each agent can be verified in isolation. Keep them.

## Current state (as of handoff)

Working:
- Skeleton runs, all three agents implemented, `/health` and `/debug/collect` OK
- NewsAPI integrated and returning articles
- Collector recently hardened: company-name mapping (AAPL -> Apple), finance-word
  query, junk-domain blocklist, title-relevance filter, dedup, over-fetch-then-filter
- Yahoo Finance RSS as a second news source. No key, no quota, 20 headlines
  for every ticker tried. It exists because NewsAPI's free tier is thin
  outside the megacaps: SHOP kept 0 articles and RIVN 5 before it, 8 and 9
  after. All news sources are filtered as one pool so dedup catches the same
  story arriving from both under different publisher names.
- 209 unit tests in `tests/` covering `_signal()`, `_weight()`, the news and
  social relevance filters, the RSS parser, the provider switch, price math,
  the run store and the evaluator. Pure functions, so they need no keys and no
  network: `pytest -q`
- **Price comes from yfinance**, not Alpha Vantage. No key, no quota, and it
  returns years of daily bars, which is what makes forward-return evaluation
  possible at all. Alpha Vantage is kept only as a fallback for when yfinance
  returns empty. `_price_from_closes()` is the pure core shared by both.
- **`app/store.py` records every run** to `data/runs.db` (SQLite, stdlib): the
  UTC decision timestamp, signal, confidence, score, item count, scorer label,
  and the price at the moment of the signal. That price is written now rather
  than looked up later, because a later lookup returns a revised or
  split-adjusted number that was never observable at decision time.
  `save()` never raises — design rule 3 covers bookkeeping too.
- **`app/evaluate.py` scores those signals against forward returns.** Entry is
  the first close STRICTLY AFTER the decision date; exit is N sessions later;
  both come from one request per ticker so they share an adjustment basis.
  HOLD is excluded from the hit rate (it makes no directional claim), VADER
  runs are excluded by default (they measure the fallback, not the model), and
  below 30 directional runs the output is flagged `conclusive: false` with the
  caveats returned in the same object as the numbers.
  Validated end to end against real June 2026 prices; entry/exit dates and
  returns line up. See "What the evaluation is worth" below.
- `app/errors.py` -> `reason(e)` extracts the provider's real message into the
  `warnings` array. It lives outside `app/agents/` because both the Analyzer and
  the Reporter use it and design rule 1 forbids cross-agent imports.
- Git: this project is its own repo now, pushed to a private GitHub remote.
  (A stray empty `.git` in the home directory used to swallow it.)

Known open issues:
- **Now running on Google Gemini's free tier** (since 2026-10-15), through
  the `openai` provider: `OPENAI_BASE_URL` points at Gemini's
  OpenAI-compatible endpoint, with `OPENAI_MODEL=gemini-3.5-flash` and
  `OPENAI_REASONING_EFFORT=minimal`. Without that last setting, its hidden
  reasoning eats the Reporter's token budget and the summary JSON comes back
  truncated. The free tier allows 20 requests per day per model; each
  `/analyze` uses 3. The OpenAI account ran out of credit.
- **Previously on OpenAI (`gpt-4o-mini`), not Claude.** The provider is
  a `.env` setting now — `LLM_PROVIDER=anthropic|openai`, resolved in
  `app/llm.py`. Agents never import a provider directly and a test enforces
  that. Switch back with one line once Anthropic credits are bought; nothing
  else changes, because the signal is computed in code either way.
- ~~LLM scoring is blocked on an empty Anthropic credit balance~~ — still true
  of the Anthropic key, worked around by switching providers. Kept below
  because the diagnosis cost real time and should not be re-run:
  RESOLVED as a diagnosis, not yet as a state. The earlier "stale model string"
  theory was wrong and is disproved: `claude-haiku-4-5-20251001` in
  `app/config.py` is valid and current, the key loads, the payload is fine.
  The real 400 body reads "Your credit balance is too low to access the
  Anthropic API." Anthropic returns **400 BadRequestError** for insufficient
  credits rather than a billing-shaped error, which is what made this look like
  a malformed request for so long.
  Fix is to buy credits at console.anthropic.com (API credits are a separate
  wallet from a Claude subscription; ~half a cent per `/analyze` run on Haiku).
  Do not go looking for this in the code again.
- VADER is a fallback only and must never be the normal path. `scorer` should
  read `llm`; if it reads `vader`, check `warnings` for the provider's real
  message. Measured against the LLM on identical headlines:
  "Stumbling Stock Rally" llm 0.00 / vader +0.78, "Alphabet vs Apple: The
  Better Buy" llm 0.00 / vader +0.87, "Bank of America doubles down on Apple"
  llm +0.60 / vader 0.00. VADER reads "Rally" and calls a stumbling market
  bullish, and misses analyst upgrades that carry no emotional words.
- ~~Alpha Vantage and Reddit keys not yet added~~ — both added and working.
  `price` populates, and the price-confirmation branch in `_signal()` now
  actually fires: AAPL and NVDA were both Bullish on the same run, but AAPL
  scored 74% confidence (price up, agrees) against NVDA's 56% (price down,
  contradicts). That branch was dead code while `trend` was always "unknown".
- `datetime.utcnow()` in `schemas.py` is deprecated and emits warnings under
  pytest. `store.py` already does it correctly with `timezone.utc`. Cosmetic
  today, breaks on a future Python.
- `/analyze` rejects non-alphanumeric tickers, so `BRK.B` is in `COMPANY_NAMES`
  but unreachable through the API. Harmless, noted so it is not a surprise.
- ~~**Reddit 403s because the IP is blocked by Reddit network security.**~~ —
  **RESOLVED 2026-08-09: the block lapsed on its own.** Raw PRAW searches now
  return posts with no warning (TSLA 4, GME 2, NVDA 7 on the day), and a full
  five-ticker run produced zero warnings. No code changed; nothing was ever
  wrong with the app or the credentials. Waiting it out was the fix.
  The diagnosis is kept because it cost real time and must not be re-run: the
  403 body was an HTML page reading "You've been blocked by network security",
  headers showed `server: snooserv` (Reddit's own edge, not Cloudflare), both
  OAuth grants returned valid tokens, and even `/api/v1/me` was refused — the
  block sat in front of the API. It was most likely triggered by burst traffic
  while the collector and social filter were being tested, so **do not hammer
  Reddit during development**; that is what caused it.
  Earlier theories — stale credentials, wrong OAuth grant, unfinished API
  registration — are all disproved. Do not re-investigate them.
  `REDDIT_PASSWORD` is intentionally blank: the password grant is the correct
  flow for a script app and the code path stays, but it is a full-account
  credential and it changed nothing here.
  Throughout the outage the pipeline degraded exactly as designed — warning
  appended, news still analyzed, signal still produced. An unplanned real
  outage that design rule 3 handled without a crash, which is the best
  evidence the rule was worth having.
- Reddit volume is low after filtering, and that is deliberate. PRAW's search
  returns loosely-related posts, so `_filter_social()` requires the ticker in
  the POST TITLE. A TSLA run kept 0 of 3 and still returned 15 news items.
  Prefer few relevant posts over many irrelevant ones; news carries volume.

## Roadmap

1. ~~Fix the LLM BadRequestError~~ — diagnosed: buy API credits, no code fix
2. ~~Verify relevance filtering~~ — it did not exist; built and measured.
   AAPL went from ~6 of 15 relevant to 15 of 15 kept from a 118-article pool.
3. ~~Add Alpha Vantage key~~ — DONE, `price` populates and the trend branch
   in `_signal()` fires for the first time.
4. ~~Add Reddit credentials~~ — DONE, plus `_filter_social()`, because PRAW
   search returned 0 of 3 relevant posts on TSLA.
5. ~~Add StockTwits as a second social source~~ — BLOCKED, and not by our code.
   `api.stocktwits.com` sits behind a Cloudflare bot challenge and returns 403
   with a "Just a moment..." HTML page to any non-browser client, regardless of
   User-Agent. The "no auth needed, public endpoint" note was out of date.
   Getting past that would mean impersonating a browser to defeat bot
   detection, so the legitimate routes are official API credentials or a
   different source. Yahoo Finance RSS was added instead — see Working.
   The Bullish/Bearish validation-set idea is still worth revisiting if
   official access is ever obtained.
6. ~~Unit tests for `_signal()` and `_weight()`~~ — DONE, 209 tests in `tests/`
7. ~~React dashboard~~ — DONE. `frontend/`, Vite + Recharts. Signal card with
   confidence meter, price card, a diverging bar chart of every item score
   with the model's reasoning on hover, and the full source list.
8. ~~Record every run~~ — DONE, `app/store.py`, SQLite, point-in-time price.
9. ~~Switch price source to yfinance~~ — DONE, no key and real history.
10. ~~Forward-return evaluation~~ — DONE, `app/evaluate.py` and
    `GET /history/evaluate`.

**This project is CLOSED as of 2026-08-09.** Everything above is done. A live
five-ticker run produced zero warnings on gpt-4o-mini, all four sources
reporting including Reddit: AAPL BUY 74%, NVDA BUY 84%, TSLA HOLD 62%,
MSFT BUY 77%, AMD HOLD 63%, all `scorer=llm`, all recorded to `data/runs.db`.

## What the evaluation is worth

Be honest about this in a demo or an interview, because an interviewer will
get there in one question and the honest answer is the stronger one.

The evaluator is real and the plumbing is correct. **The result is not a
result.** There are a handful of recorded runs, all still inside their
horizon, so nothing has resolved. Even after months of daily runs the sample
would be far too small to distinguish a real edge from noise, and none of it
is adjusted for transaction costs, slippage, or a market benchmark. News
sentiment is also the most-tried retail signal there is; the prior that any
edge survives an LLM-latency pipeline is low.

That is why `conclusive` is a field in the response and why the caveats ship
in the same object as the numbers. The point of this module was never to
prove an edge — it was to build the thing that could detect one, and to avoid
the three standard ways of fooling yourself while doing it: look-ahead on
entry, HOLD padding the hit rate, and a re-adjusted price series turning a
split into a fake loss. All three are guarded and unit-tested.

"I measured my own signal and the sample was too small to conclude anything"
is the correct finding, and it is a better thing to be able to say than a
hit rate would be.

## If work resumes

Nothing here is blocking, and none of it is on the critical path:
- No caching. Every request refetches everything and re-scores it.
- Switch back to Claude with `LLM_PROVIDER=anthropic` once credits are bought.
- The dashboard does not surface `/history` or `/history/evaluate` at all.
- Keep running `/analyze` occasionally to accumulate evaluable history.
- `datetime.utcnow()` in `schemas.py` should become `now(timezone.utc)`.

## Working preferences

Direct, no fluff. Concrete over theoretical. Explain the "why" behind a change,
not just the diff. Small verifiable steps — get one thing working end to end
before widening. Commit as you go.
