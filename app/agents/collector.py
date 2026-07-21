"""
AGENT 1 - COLLECTOR

No LLM. Pure data gathering. Hits three sources concurrently and
normalizes everything into one CollectedData object.

Design rule: this agent never fails hard. If one source is down,
it records a warning and returns what it has.
"""
import asyncio
import logging
from datetime import datetime, timedelta

import httpx

from app.config import settings
from app.schemas import CollectedData, ContentItem, PriceData

log = logging.getLogger(__name__)


async def _fetch_news(ticker: str) -> tuple[list[ContentItem], list[str]]:
    if not settings.newsapi_key:
        return [], ["NEWSAPI_KEY missing - skipped news"]

    since = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": f"{ticker} stock",
        "from": since,
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": settings.news_limit,
        "apiKey": settings.newsapi_key,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        log.warning("news fetch failed: %s", e)
        return [], [f"news unavailable: {e}"]

    items = []
    for a in data.get("articles", []):
        items.append(ContentItem(
            source=(a.get("source") or {}).get("name", "unknown"),
            kind="news",
            title=a.get("title") or "",
            text=(a.get("description") or "")[:1000],
            url=a.get("url") or "",
            published_at=a.get("publishedAt"),
        ))
    return items, []


def _fetch_reddit_sync(ticker: str) -> tuple[list[ContentItem], list[str]]:
    """PRAW is synchronous, so this runs in a thread."""
    if not settings.reddit_client_id:
        return [], ["REDDIT credentials missing - skipped reddit"]
    try:
        import praw
        reddit = praw.Reddit(
            client_id=settings.reddit_client_id,
            client_secret=settings.reddit_client_secret,
            user_agent=settings.reddit_user_agent,
        )
        sub = reddit.subreddit(settings.subreddits)
        items = []
        for post in sub.search(ticker, sort="new", time_filter="week",
                               limit=settings.reddit_limit):
            items.append(ContentItem(
                source=f"r/{post.subreddit.display_name}",
                kind="reddit",
                title=post.title or "",
                text=(post.selftext or "")[:1000],
                url=f"https://reddit.com{post.permalink}",
                published_at=datetime.utcfromtimestamp(post.created_utc).isoformat(),
                popularity=int(post.score or 0),
            ))
        return items, []
    except Exception as e:
        log.warning("reddit fetch failed: %s", e)
        return [], [f"reddit unavailable: {e}"]


async def _fetch_reddit(ticker: str):
    return await asyncio.to_thread(_fetch_reddit_sync, ticker)


async def _fetch_price(ticker: str) -> tuple[PriceData, list[str]]:
    if not settings.alphavantage_key:
        return PriceData(), ["ALPHAVANTAGE_KEY missing - skipped price"]

    url = "https://www.alphavantage.co/query"
    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": ticker,
        "outputsize": "compact",
        "apikey": settings.alphavantage_key,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            series = r.json().get("Time Series (Daily)", {})
    except Exception as e:
        return PriceData(), [f"price unavailable: {e}"]

    if not series:
        return PriceData(), ["price data empty (rate limit or bad ticker)"]

    days = sorted(series.keys(), reverse=True)
    current = float(series[days[0]]["4. close"])
    ref = days[min(5, len(days) - 1)]
    week_ago = float(series[ref]["4. close"])
    change = ((current - week_ago) / week_ago) * 100 if week_ago else 0.0
    trend = "up" if change > 1 else "down" if change < -1 else "flat"

    return PriceData(current=round(current, 2), week_ago=round(week_ago, 2),
                     change_pct=round(change, 2), trend=trend), []


async def run(ticker: str) -> CollectedData:
    """Entry point for Agent 1."""
    ticker = ticker.strip().upper()
    news, reddit, price = await asyncio.gather(
        _fetch_news(ticker), _fetch_reddit(ticker), _fetch_price(ticker)
    )
    items = news[0] + reddit[0]
    warnings = news[1] + reddit[1] + price[1]
    if not items:
        warnings.append("no content collected - check API keys")

    return CollectedData(ticker=ticker, items=items, price=price[0],
                         warnings=warnings)
