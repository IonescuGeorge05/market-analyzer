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
| Collector | `app/agents/collector.py` | no | NewsAPI + Yahoo Finance RSS + Reddit (PRAW) + Alpha Vantage, fetched concurrently, normalized, relevance-filtered |
| Analyzer | `app/agents/analyzer.py` | yes | Scores each item -1..+1, weighted aggregate, VADER fallback |
| Reporter | `app/agents/reporter.py` | yes | Writes the prose summary; signal computed in code |

Orchestration is `app/pipeline.py` — three sequential awaits, nothing clever.

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

## Endpoints

- `GET /analyze/{ticker}` — full pipeline, the real endpoint
- `GET /debug/collect/{ticker}` — Agent 1 only
- `GET /debug/analyze/{ticker}` — Agents 1+2
- `GET /health`

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
- 111 unit tests in `tests/` covering `_signal()`, `_weight()`, the news and
  social relevance filters, the RSS parser and the provider switch. Pure functions, so they need no keys and no
  network: `pytest -q`
- `app/errors.py` -> `reason(e)` extracts the provider's real message into the
  `warnings` array. It lives outside `app/agents/` because both the Analyzer and
  the Reporter use it and design rule 1 forbids cross-agent imports.
- Git: this project is its own repo now, pushed to a private GitHub remote.
  (A stray empty `.git` in the home directory used to swallow it.)

Known open issues:
- **Currently running on OpenAI (`gpt-4o-mini`), not Claude.** The provider is
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
- **Reddit 403s because the IP is blocked by Reddit network security.**
  The 403 body is an HTML page whose visible text reads "You've been blocked
  by network security. If you think you've been blocked by mistake, file a
  ticket below." Headers show `server: snooserv`, so it is Reddit's own edge,
  not Cloudflare.
  Nothing is wrong with the app or the credentials. Both OAuth grants return
  valid tokens (the password grant's JWT carries sub=user), the app shows
  under "authorized applications" with Read Content granted, and yet even
  `/api/v1/me` is refused. The block sits in front of the API.
  It also explains the captcha looping on reddit.com/prefs/apps - the same
  network block hits the web UI.
  Likely triggered by burst traffic while the collector and social filter
  were being tested. Remedies are to file the ticket Reddit links from that
  page, or wait for it to lapse. Earlier theories in this file - stale
  credentials, wrong OAuth grant, unfinished API registration - are all
  disproved; do not re-investigate them.
  `REDDIT_PASSWORD` is intentionally blank: the password grant is the correct
  flow for a script app and the code path stays, but it is a full-account
  credential and it changed nothing here.
  Throughout, the pipeline degraded exactly as designed - warning appended,
  news still analyzed, signal still produced. An unplanned real outage that
  design rule 3 handled without a crash.
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
6. ~~Unit tests for `_signal()` and `_weight()`~~ — DONE, 111 tests in `tests/`
7. ~~React dashboard~~ — DONE. `frontend/`, Vite + Recharts. Signal card with
   confidence meter, price card, a diverging bar chart of every item score
   with the model's reasoning on hover, and the full source list.

All seven items are done. The pipeline runs end to end with no warnings on
gpt-4o-mini and the dashboard renders it: `GET /analyze/AAPL` returns BUY at
77%, `scorer` reads `llm`, and all four sources report.

Next things worth doing, none of them blocking:
- No caching. Every request refetches everything and re-scores it.
- Alpha Vantage free tier is 25 requests/day, so a live demo can exhaust it.
- Switch back to Claude with `LLM_PROVIDER=anthropic` once credits are bought.

## Working preferences

Direct, no fluff. Concrete over theoretical. Explain the "why" behind a change,
not just the diff. Small verifiable steps — get one thing working end to end
before widening. Commit as you go.
