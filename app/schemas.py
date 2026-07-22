"""
The JSON contracts between agents.

This file IS the interface. Agent 1 must produce CollectedData.
Agent 2 consumes CollectedData and produces SentimentAnalysis.
Agent 3 consumes SentimentAnalysis and produces Report.

If you change a schema here, both sides of that boundary must agree.
"""
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field


# ---------- Agent 1 output ----------

class ContentItem(BaseModel):
    """One piece of text to be scored. News article or Reddit post."""
    source: str                      # "reuters" or "r/wallstreetbets"
    kind: Literal["news", "reddit"]
    title: str
    text: str = ""
    url: str = ""
    published_at: Optional[str] = None
    popularity: int = 0              # reddit upvotes; 0 for news


class PriceData(BaseModel):
    current: Optional[float] = None
    week_ago: Optional[float] = None
    change_pct: Optional[float] = None
    trend: Literal["up", "down", "flat", "unknown"] = "unknown"


class CollectedData(BaseModel):
    ticker: str
    collected_at: datetime = Field(default_factory=datetime.utcnow)
    items: list[ContentItem] = []
    price: PriceData = PriceData()
    warnings: list[str] = []         # e.g. "reddit unavailable"


# ---------- Agent 2 output ----------

class ScoredItem(BaseModel):
    source: str
    kind: Literal["news", "reddit"]
    title: str
    score: float                     # -1.0 bearish .. +1.0 bullish
    reasoning: str = ""
    weight: float = 1.0
    scorer: Literal["llm", "vader"] = "llm"


class SentimentAnalysis(BaseModel):
    ticker: str
    overall_score: float
    classification: Literal["Bullish", "Neutral", "Bearish"]
    scored_items: list[ScoredItem] = []
    top_bullish: list[str] = []
    top_bearish: list[str] = []
    price: PriceData = PriceData()
    item_count: int = 0
    warnings: list[str] = []


# ---------- Agent 3 output ----------

class Report(BaseModel):
    ticker: str
    signal: Literal["BUY", "HOLD", "SELL"]
    confidence: int                  # 0-100
    overall_score: float
    classification: str
    summary: str
    key_drivers: list[str] = []
    risk_factors: list[str] = []
    markdown: str = ""
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    warnings: list[str] = []

    # Carried through from SentimentAnalysis so one /analyze call is enough to
    # render the dashboard. Without these the frontend would have to call
    # /debug/analyze as well, paying for the LLM scoring twice to show the
    # evidence behind a signal it was already given.
    price: PriceData = PriceData()
    scored_items: list[ScoredItem] = []
    item_count: int = 0
