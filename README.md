# Market Sentiment Analyzer

A three-agent pipeline that turns a ticker into a BUY/HOLD/SELL signal from
news and social sentiment — and then scores its own past signals against what
prices actually did.

**Not investment advice.** This analyzes public sentiment. It is a software
project, not a trading system. See [What the evaluation is worth](#what-the-evaluation-is-worth).

## Architecture

    ticker -> [Agent 1: Collector] -> CollectedData
                                          |
                                          v
                     [Agent 2: Analyzer] -> SentimentAnalysis
                                          |
                                          v
                     [Agent 3: Reporter] -> Report (signal + summary)
                                          |
                                          v
                              store.py -> data/runs.db
                                          |
                                          v
                              evaluate.py -> forward returns

Each agent talks to the next only through a Pydantic schema in `app/schemas.py`.
No agent imports another agent's internals.

| Agent | LLM? | Job |
|---|---|---|
| Collector | no | NewsAPI + Yahoo Finance RSS + Reddit + yfinance, fetched concurrently, normalized, relevance-filtered |
| Analyzer | yes | Scores each item -1..+1, weighted aggregate, VADER fallback |
| Reporter | yes | Writes the summary; signal computed deterministically in code |

`store.py` and `evaluate.py` live outside `app/agents/` because the pipeline
writes them and the API reads them, and no agent may import another's internals.

## Design decisions worth defending

- **The signal is code, not model output.** The Reporter's LLM writes prose;
  `_signal()` in `reporter.py` decides BUY/HOLD/SELL from numbers. Same input →
  same output, and it is unit-tested.
- **Point-in-time price capture.** Every run records the price *at the moment
  of the signal*, because looking it up later returns a revised or
  split-adjusted number that was never observable at decision time.
- **No look-ahead on entry.** The evaluator enters at the first close *strictly
  after* the decision date — never the same session, whose close may already
  have passed when the signal was made.
- **One price request per ticker.** Entry and exit closes come from the same
  fetch so they share an adjustment basis. Fetching them separately turns a 2:1
  split into a fictional −50% return.
- **HOLD is excluded from the hit rate.** It makes no directional claim, and
  counting HOLDs as wins is the easiest way to manufacture a good number.
- **Weighted aggregation.** News weighs 1.0, Reddit 0.3–0.7 by upvotes, so one
  viral post cannot flip the signal.
- **Every agent degrades.** A dead source appends a warning and the pipeline
  still returns, with confidence reduced to match. This was not theoretical —
  Reddit blocked our IP for several days and the pipeline never once crashed.
- **Batched LLM calls.** 8 items per call instead of one call per item.

## Tests

    pytest -q        # 209 tests, no keys and no network required

Every filter, the signal rule, the weighting, the price math, the RSS parser,
the provider switch, the run store and the evaluator are pure functions, and
tested as such.

## Setup

    python -m venv .venv
    source .venv/bin/activate        # Windows: .venv\Scripts\activate
    pip install -r requirements.txt
    cp .env.example .env             # then fill in your keys

### Keys

| Key | Where | Free tier | Required? |
|---|---|---|---|
| `OPENAI_API_KEY` *or* `ANTHROPIC_API_KEY` | platform.openai.com / console.anthropic.com | pay as you go, ~½¢ per run | yes |
| `NEWSAPI_KEY` | newsapi.org | 100 req/day | yes |
| `REDDIT_CLIENT_ID` / `SECRET` | reddit.com/prefs/apps → create app → "script" | generous | optional |
| `ALPHAVANTAGE_KEY` | alphavantage.co | 25 req/day | optional, fallback only |

Yahoo Finance RSS and yfinance need no key at all. The system degrades
gracefully: missing keys produce warnings, not crashes.

Switch LLM provider with one line — `LLM_PROVIDER=anthropic|openai`. The
BUY/HOLD/SELL rule is computed in code either way, so the decision does not
change with the provider.

## Run

    uvicorn app.main:app --reload     # :8000
    cd frontend && npm run dev        # :5173

| Endpoint | What |
|---|---|
| `GET /analyze/{ticker}` | Full pipeline. The real endpoint. |
| `GET /history` | Past runs, newest first |
| `GET /history/stats` | How much has been recorded |
| `GET /history/evaluate` | Score past signals against forward returns |
| `GET /debug/collect/{ticker}` | Agent 1 only |
| `GET /debug/analyze/{ticker}` | Agents 1+2 |
| `GET /health` | Liveness |

Docs and a live try-it UI at http://localhost:8000/docs

The debug endpoints exist so each agent can be verified in isolation.

## What the evaluation is worth

`/history/evaluate` reports a hit rate, mean forward return, and breakdowns by
signal and by confidence bucket. It also reports `conclusive: false` and ships
its caveats in the same object as its numbers, because as things stand:

- the recorded sample is far too small to distinguish an edge from noise;
- nothing is adjusted for transaction costs, slippage, or a market benchmark;
- news sentiment is the most-tried retail signal there is, and the prior that
  any edge survives an LLM-latency pipeline is low.

**The evaluator is real; the result is not a result.** The point of building it
was never to demonstrate an edge — it was to build the thing that could detect
one, and to avoid the three standard ways of fooling yourself while doing it:
look-ahead on entry, HOLD padding the hit rate, and a re-adjusted price series
turning a split into a fake loss. All three are guarded and unit-tested.

Refusing to draw a conclusion from a thin sample is the part that matters.

## Not investment advice

Public sentiment analysis, built as a software engineering exercise. Do not
trade on it.
