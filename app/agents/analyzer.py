"""
AGENT 2 - ANALYZER

Scores every collected item -1.0 (bearish) to +1.0 (bullish) using Claude,
then aggregates into one weighted score.

Batching note: we send items in batches instead of one call per item.
15 items = 2 calls instead of 15. Cheaper and much faster.

Fallback: if Claude fails, VADER (rule-based) scores everything so the
pipeline still returns something usable.
"""
import json
import logging

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import settings
from app.errors import reason
from app.schemas import CollectedData, ScoredItem, SentimentAnalysis

log = logging.getLogger(__name__)

BATCH_SIZE = 8

SYSTEM_PROMPT = """You are a financial sentiment analyst.

You will receive a numbered list of texts (news headlines or social posts) about a stock.
Score each one for sentiment toward that stock's future price:

  -1.0 = extremely bearish   0.0 = neutral/irrelevant   +1.0 = extremely bullish

Rules:
- Judge sentiment about the STOCK, not general tone. "Company sued" is bearish even if written neutrally.
- Irrelevant or off-topic text scores 0.0.
- Ignore hype and emojis; weigh substance. A rocket emoji is not evidence.
- reasoning must be one short clause, max 12 words.

Return ONLY a JSON array, no prose, no markdown fences:
[{"i": 1, "score": 0.6, "reasoning": "earnings beat expectations"}, ...]
Include every item you were given, in order."""


def _llm():
    return ChatAnthropic(
        model=settings.llm_model,
        api_key=settings.anthropic_api_key,
        temperature=0,
        max_tokens=2000,
    )


def _weight(item) -> float:
    """News is more reliable than Reddit; popular Reddit posts count more."""
    if item.kind == "news":
        return 1.0
    if item.popularity > 1000:
        return 0.7
    if item.popularity > 100:
        return 0.5
    return 0.3


def _vader_scores(items) -> list[tuple[float, str]]:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    an = SentimentIntensityAnalyzer()
    out = []
    for it in items:
        c = an.polarity_scores(f"{it.title}. {it.text}")["compound"]
        out.append((round(c, 3), "vader fallback"))
    return out


async def _score_batch(llm, batch) -> list[tuple[float, str]]:
    listing = "\n".join(
        f"{n}. [{it.kind}] {it.title} :: {it.text[:300]}"
        for n, it in enumerate(batch, 1)
    )
    resp = await llm.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=listing),
    ])
    raw = resp.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].removeprefix("json").strip()
    parsed = json.loads(raw)

    by_index = {int(o["i"]): o for o in parsed}
    out = []
    for n in range(1, len(batch) + 1):
        o = by_index.get(n, {})
        score = max(-1.0, min(1.0, float(o.get("score", 0.0))))
        out.append((score, str(o.get("reasoning", ""))[:120]))
    return out


async def run(data: CollectedData) -> SentimentAnalysis:
    """Entry point for Agent 2."""
    warnings = list(data.warnings)
    items = data.items

    if not items:
        return SentimentAnalysis(
            ticker=data.ticker, overall_score=0.0, classification="Neutral",
            price=data.price, item_count=0,
            warnings=warnings + ["nothing to analyze"],
        )

    results: list[tuple[float, str]] = []
    scorer = "llm"
    try:
        llm = _llm()
        for i in range(0, len(items), BATCH_SIZE):
            results += await _score_batch(llm, items[i:i + BATCH_SIZE])
    except Exception as e:
        log.warning("LLM scoring failed, falling back to VADER: %s", e)
        # Surface the provider's actual message, not just the exception class.
        # "BadRequestError" alone hides why (bad model string? no credits?).
        warnings.append(
            f"LLM scoring failed ({type(e).__name__}: {reason(e)}) - used VADER"
        )
        results = _vader_scores(items)
        scorer = "vader"

    scored = [
        ScoredItem(source=it.source, kind=it.kind, title=it.title,
                   score=sc, reasoning=why, weight=_weight(it), scorer=scorer)
        for it, (sc, why) in zip(items, results)
    ]

    total_w = sum(s.weight for s in scored) or 1.0
    overall = sum(s.score * s.weight for s in scored) / total_w
    overall = round(overall, 3)

    classification = ("Bullish" if overall > 0.15
                      else "Bearish" if overall < -0.15 else "Neutral")

    ranked = sorted(scored, key=lambda s: s.score, reverse=True)
    top_bullish = [f"{s.title} ({s.score:+.2f})" for s in ranked[:3] if s.score > 0.1]
    top_bearish = [f"{s.title} ({s.score:+.2f})" for s in ranked[::-1][:3] if s.score < -0.1]

    return SentimentAnalysis(
        ticker=data.ticker, overall_score=overall, classification=classification,
        scored_items=scored, top_bullish=top_bullish, top_bearish=top_bearish,
        price=data.price, item_count=len(scored), warnings=warnings,
    )
