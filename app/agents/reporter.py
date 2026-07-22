"""
AGENT 3 - REPORTER

Turns scores into a human-readable report plus a BUY/HOLD/SELL signal.

Important design choice: the SIGNAL IS COMPUTED IN CODE, not by the LLM.
The LLM writes the prose; deterministic rules decide the call. This means
the same numbers always give the same signal, which you can test and defend.
"""
import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from app.errors import reason
from app.llm import chat
from app.schemas import Report, SentimentAnalysis

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a financial analyst writing a brief sentiment note.

You get aggregated sentiment data about one ticker. Write a factual summary
of what the sentiment picture shows.

Rules:
- 100-150 words for the summary. Plain, sober language. No hype.
- Describe what the data shows; do not invent facts that are not in the input.
- If the item count is low or sources are missing, say the read is weak.
- This is analysis of public sentiment, not investment advice.

Return ONLY JSON, no markdown fences:
{"summary": "...", "key_drivers": ["...", "..."], "risk_factors": ["...", "..."]}
key_drivers and risk_factors: 2-4 short bullets each."""


def _signal(a: SentimentAnalysis) -> tuple[str, int]:
    """Deterministic rules. Sentiment sets direction, price trend confirms."""
    s = a.overall_score
    trend = a.price.trend

    if s > 0.15:
        signal = "BUY"
    elif s < -0.15:
        signal = "SELL"
    else:
        signal = "HOLD"

    # Confidence: strength of signal, volume of evidence, price agreement
    conf = 40
    conf += min(30, int(abs(s) * 60))
    conf += min(15, a.item_count)
    if (signal == "BUY" and trend == "up") or (signal == "SELL" and trend == "down"):
        conf += 10
    elif (signal == "BUY" and trend == "down") or (signal == "SELL" and trend == "up"):
        conf -= 15
    if a.item_count < 5:
        conf -= 20
    if any("failed" in w or "unavailable" in w for w in a.warnings):
        conf -= 10

    return signal, max(5, min(95, conf))


def _markdown(a: SentimentAnalysis, signal: str, conf: int,
              summary: str, drivers: list[str], risks: list[str]) -> str:
    p = a.price
    price_line = (f"${p.current} ({p.change_pct:+.2f}% over 5 sessions, {p.trend})"
                  if p.current else "unavailable")
    lines = [
        f"# {a.ticker} - Market Sentiment Report",
        "",
        f"**Signal:** {signal}  |  **Confidence:** {conf}%",
        f"**Sentiment:** {a.classification} ({a.overall_score:+.2f})",
        f"**Price:** {price_line}",
        f"**Sources analyzed:** {a.item_count}",
        "", "## Summary", "", summary, "", "## Key drivers", "",
        *[f"- {d}" for d in drivers],
        "", "## Risk factors", "",
        *[f"- {r}" for r in risks],
    ]
    if a.warnings:
        lines += ["", "## Warnings", "", *[f"- {w}" for w in a.warnings]]
    lines += ["", "---", "*Sentiment analysis of public sources. Not investment advice.*"]
    return "\n".join(lines)


async def run(a: SentimentAnalysis) -> Report:
    """Entry point for Agent 3."""
    signal, conf = _signal(a)
    warnings = list(a.warnings)

    payload = {
        "ticker": a.ticker,
        "overall_score": a.overall_score,
        "classification": a.classification,
        "item_count": a.item_count,
        "top_bullish": a.top_bullish,
        "top_bearish": a.top_bearish,
        "price": a.price.model_dump(),
        "computed_signal": signal,
    }

    summary, drivers, risks = "", [], []
    try:
        # Slight temperature here: this is prose, not scoring.
        llm = chat(temperature=0.3, max_tokens=1200)
        resp = await llm.ainvoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=json.dumps(payload, indent=2)),
        ])
        raw = resp.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].removeprefix("json").strip()
        out = json.loads(raw)
        summary = str(out.get("summary", ""))
        drivers = [str(x) for x in out.get("key_drivers", [])][:4]
        risks = [str(x) for x in out.get("risk_factors", [])][:4]
    except Exception as e:
        log.warning("report generation failed: %s", e)
        warnings.append(
            f"report LLM failed ({type(e).__name__}: {reason(e)}) - using template"
        )
        summary = (f"{a.ticker} sentiment is {a.classification.lower()} "
                   f"at {a.overall_score:+.2f} across {a.item_count} sources.")
        drivers = a.top_bullish or ["no clear bullish drivers"]
        risks = a.top_bearish or ["no clear bearish drivers"]

    return Report(
        ticker=a.ticker, signal=signal, confidence=conf,
        overall_score=a.overall_score, classification=a.classification,
        summary=summary, key_drivers=drivers, risk_factors=risks,
        markdown=_markdown(a, signal, conf, summary, drivers, risks),
        warnings=warnings,
    )
