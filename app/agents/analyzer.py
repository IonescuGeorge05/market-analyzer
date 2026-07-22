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
import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.errors import reason
from app.llm import chat
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
Include every item you were given, in order.
Write positive scores as 0.6, never as +0.6 - a leading + is not valid JSON."""


def _llm():
    """Temperature 0: scoring must be as reproducible as the provider allows."""
    return chat(temperature=0, max_tokens=2000)


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


def _extract_json_array(raw: str) -> list:
    """Pull the JSON array out of a model response.

    The prompt asks for bare JSON and usually gets it, but not always: a
    response occasionally arrives fenced, or with a sentence of preamble, and
    a bare json.loads then fails on the whole batch. Slicing from the first
    "[" to the last "]" survives all three shapes and costs nothing when the
    response was already clean.
    """
    text = raw.strip()
    if text.startswith("```"):
        # ```json\n[...]\n``` -> take the fenced body, whatever the label
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        text = text.removeprefix("json").removeprefix("JSON").strip()

    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON array in model response: {raw[:120]!r}")
    body = text[start:end + 1]

    # Strip a leading + from numbers. The model writes "score": +1.0 for
    # emphatically bullish items, which is not legal JSON - the spec allows a
    # leading minus and nothing else. This was an intermittent failure that
    # only bit when a batch happened to contain a strongly positive score,
    # and it silently cost eight real scores each time it did.
    body = re.sub(r'([:\[,]\s*)\+(\d)', r'\1\2', body)

    return json.loads(body)


async def _score_batch(llm, batch) -> list[tuple[float, str]]:
    listing = "\n".join(
        f"{n}. [{it.kind}] {it.title} :: {it.text[:300]}"
        for n, it in enumerate(batch, 1)
    )
    resp = await llm.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=listing),
    ])
    parsed = _extract_json_array(resp.content)

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

    # Fall back per batch, not for the whole run. One malformed response used
    # to drop every item to VADER, so a single bad batch of 8 discarded good
    # LLM scores for everything else. ScoredItem.scorer is per item, so a
    # partial fallback is visible in the response rather than hidden.
    results: list[tuple[float, str]] = []
    scorers: list[str] = []
    failures: list[str] = []

    llm = None
    try:
        llm = _llm()
    except Exception as e:
        log.warning("could not build LLM client: %s", e)
        failures.append(f"{type(e).__name__}: {reason(e)}")

    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i:i + BATCH_SIZE]
        if llm is not None:
            try:
                results += await _score_batch(llm, batch)
                scorers += ["llm"] * len(batch)
                continue
            except Exception as e:
                log.warning("LLM scoring failed on batch %d, using VADER: %s",
                            i // BATCH_SIZE + 1, e)
                failures.append(f"{type(e).__name__}: {reason(e)}")
        results += _vader_scores(batch)
        scorers += ["vader"] * len(batch)

    if failures:
        # One warning per distinct cause, not one per batch.
        for cause in dict.fromkeys(failures):
            warnings.append(f"LLM scoring failed ({cause}) - used VADER")

    scored = [
        ScoredItem(source=it.source, kind=it.kind, title=it.title,
                   score=sc, reasoning=why, weight=_weight(it), scorer=sc_name)
        for it, (sc, why), sc_name in zip(items, results, scorers)
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
