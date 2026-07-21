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
| Collector | `app/agents/collector.py` | no | NewsAPI + Reddit (PRAW) + Alpha Vantage, fetched concurrently, normalized, relevance-filtered |
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

Python 3.11+, FastAPI, LangChain (`langchain-anthropic`), Claude as the LLM,
Pydantic v2 schemas. React + Recharts planned for the dashboard, not built yet.

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

Known open issues:
- **Claude scoring returns BadRequestError and silently falls back to VADER.**
  Likely a stale model string in `app/config.py`. Diagnose via the uvicorn log
  line "LLM scoring failed, falling back to VADER: ...". The tell is `scorer`
  in the response saying `vader` instead of `llm`. THIS IS THE TOP PRIORITY.
- VADER scores are poor (it scored a "Poor Stock Performance" headline at +0.93).
  It is a fallback only and must never be the normal path.
- Alpha Vantage and Reddit keys not yet added, so `price` is null and there is
  no social data. Both are expected warnings right now, not bugs.

## Roadmap

1. Fix the LLM BadRequestError so scoring runs on Claude
2. Verify relevance filtering actually improved article quality
3. Add Alpha Vantage key -> price context populated
4. Add Reddit credentials -> social source live
5. Add StockTwits as a second social source (no auth needed, public endpoint,
   ticker-tagged, and users self-label Bullish/Bearish — useful as a ground-truth
   check against our own LLM scores)
6. Unit tests for `_signal()` and `_weight()` — pure functions, easy wins
7. React dashboard last

## Working preferences

Direct, no fluff. Concrete over theoretical. Explain the "why" behind a change,
not just the diff. Small verifiable steps — get one thing working end to end
before widening. Commit as you go.
