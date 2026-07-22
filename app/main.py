"""FastAPI entrypoint.  Run:  uvicorn app.main:app --reload"""
import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.agents import analyzer, collector
from app.pipeline import run_pipeline
from app.schemas import CollectedData, Report, SentimentAnalysis

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

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
