"""FastAPI entrypoint.  Run:  uvicorn app.main:app --reload"""
import asyncio
import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app import evaluate, store
from app.agents import analyzer, collector
from app.pipeline import run_pipeline
from app.schemas import CollectedData, Report, SentimentAnalysis

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# httpx logs every request at INFO with the FULL URL, query string included.
# NewsAPI and Alpha Vantage both take their key as a query parameter, so at
# INFO the logs read:
#
#   GET https://newsapi.org/v2/everything?q=...&apiKey=<your real key>
#
# That is a live credential in plaintext in any terminal, screenshot, pasted
# log or aggregated log stream. Our own logs already report what matters
# ("news: 119 fetched -> 15 kept"), so nothing is lost by silencing it.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

app = FastAPI(title="Market Sentiment Analyzer", version="0.1.0")

# Any localhost port, not a fixed list. Vite silently moves to 5174 when 5173
# is taken, and a hardcoded allowlist then makes the browser discard a
# perfectly good 200 response - the server logs success while the UI shows
# "Failed to fetch", which is a genuinely confusing way to lose an afternoon.
# This is a dev convenience; a deployed frontend would get an explicit origin.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"], allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/analyze/{ticker}", response_model=Report)
async def analyze(ticker: str):
    """Full 3-agent pipeline. This is the main endpoint."""
    if not ticker.isalnum() or len(ticker) > 10:
        raise HTTPException(400, "invalid ticker")
    return await run_pipeline(ticker)


# --- Debug endpoints: test each agent in isolation during development ---

@app.get("/debug/collect/{ticker}", response_model=CollectedData)
async def debug_collect(ticker: str):
    return await collector.run(ticker)


@app.get("/debug/analyze/{ticker}", response_model=SentimentAnalysis)
async def debug_analyze(ticker: str):
    return await analyzer.run(await collector.run(ticker))


# --- Run history: the record every analysis is written to, so the signal can
#     be evaluated later against what prices actually did. ---

@app.get("/history")
async def get_history(ticker: str | None = None, limit: int = 100):
    """Past runs, newest first. Optional ?ticker= filter."""
    return {"runs": store.history(ticker=ticker, limit=limit)}


@app.get("/history/stats")
async def get_history_stats():
    """How much has been recorded so far."""
    return store.stats()


@app.get("/history/evaluate")
async def get_history_evaluation(ticker: str | None = None,
                                 horizon: int = evaluate.HORIZON_SESSIONS,
                                 llm_only: bool = True):
    """Score past signals against what prices actually did.

    Returns the hit rate and mean forward return, broken down by signal and by
    confidence bucket - and, in the same object, the caveats that say why those
    numbers may not mean what you want them to. Reading one without the other
    is the mistake this endpoint exists to prevent.

    Runs on a worker thread: it fetches one price history per ticker, and
    blocking the event loop on that would stall concurrent /analyze calls.
    """
    if not 1 <= horizon <= 60:
        raise HTTPException(400, "horizon must be 1-60 sessions")
    return await asyncio.to_thread(
        evaluate.run_evaluation, ticker=ticker, horizon=horizon,
        llm_only=llm_only)
