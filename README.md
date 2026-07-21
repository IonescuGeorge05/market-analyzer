# Market Sentiment Analyzer

Three-agent pipeline. Input a ticker, get a BUY/HOLD/SELL signal backed by
news and social sentiment.

## Architecture

    ticker -> [Agent 1: Collector] -> CollectedData
                                          |
                                          v
                     [Agent 2: Analyzer] -> SentimentAnalysis
                                          |
                                          v
                     [Agent 3: Reporter] -> Report (signal + summary)

Each agent talks to the next only through a Pydantic schema in `app/schemas.py`.
No agent imports another agent's internals.

| Agent | LLM? | Job |
|---|---|---|
| Collector | no | NewsAPI + Reddit (PRAW) + Alpha Vantage, concurrent, normalized |
| Analyzer | yes | Scores each item -1..+1, weighted aggregate, VADER fallback |
| Reporter | yes | Writes the summary; signal computed deterministically in code |

## Setup

    python -m venv .venv
    source .venv/bin/activate        # Windows: .venv\Scripts\activate
    pip install -r requirements.txt
    cp .env.example .env             # then fill in your keys

### Keys needed

| Key | Where | Free tier |
|---|---|---|
| ANTHROPIC_API_KEY | console.anthropic.com | pay as you go, ~$1 covers dev |
| NEWSAPI_KEY | newsapi.org | 100 req/day |
| ALPHAVANTAGE_KEY | alphavantage.co/support/#api-key | 25 req/day |
| REDDIT_CLIENT_ID / SECRET | reddit.com/prefs/apps -> create app -> "script" | unlimited-ish |

The system degrades gracefully: missing keys produce warnings, not crashes.

## Run

    uvicorn app.main:app --reload

- Docs / try it: http://localhost:8000/docs
- Full pipeline: http://localhost:8000/analyze/AAPL
- Agent 1 only:  http://localhost:8000/debug/collect/AAPL
- Agents 1+2:    http://localhost:8000/debug/analyze/AAPL

## Build order

1. Get `/debug/collect/AAPL` returning real items. Only needs NewsAPI. (Sprint 1)
2. Add Reddit and price, verify normalization. (Sprint 1)
3. Get `/debug/analyze/AAPL` returning scores. (Sprint 2)
4. Get `/analyze/AAPL` returning a full report. (Sprint 3)
5. React dashboard on top. (Sprint 4)

## Design notes worth defending in a demo

- **Signal is code, not LLM.** The Reporter's LLM writes prose; `_signal()` in
  `reporter.py` decides BUY/HOLD/SELL from numbers. Same input -> same output,
  and it is unit-testable.
- **Weighted aggregation.** News weighs 1.0, Reddit 0.3-0.7 by upvotes, so one
  viral shitpost cannot flip the signal.
- **Batched LLM calls.** 8 items per call instead of one call per item.
- **Every agent degrades.** Source down -> warning in the response, pipeline
  still returns. Confidence drops when evidence is thin.

## Not investment advice

This analyzes public sentiment. It is a software project, not a trading system.
