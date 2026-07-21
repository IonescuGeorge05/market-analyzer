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


# --- Relevance filtering -----------------------------------------------------
#
# NewsAPI's `q` is a plain keyword match, so "AAPL stock" returns PyPI package
# listings, Show HN posts and crypto articles that merely contain the word.
# Measured on a live AAPL fetch: only ~6 of 15 articles were actually about
# Apple. Everything the analyzer scores costs a token and drags the weighted
# average toward noise, so the cheapest quality win is to not collect junk.
#
# Every helper below is a pure function, which keeps them unit-testable without
# a network or an API key.

# Ticker -> company name. NewsAPI matches prose, and prose says "Apple", not
# "AAPL", so searching the name finds far more real coverage than the symbol.
COMPANY_NAMES = {
    "AAPL": "Apple", "MSFT": "Microsoft", "GOOGL": "Alphabet", "GOOG": "Alphabet",
    "AMZN": "Amazon", "TSLA": "Tesla", "META": "Meta", "NVDA": "Nvidia",
    "NFLX": "Netflix", "AMD": "AMD", "INTC": "Intel", "IBM": "IBM",
    "ORCL": "Oracle", "CRM": "Salesforce", "UBER": "Uber", "DIS": "Disney",
    "BA": "Boeing", "JPM": "JPMorgan", "V": "Visa", "WMT": "Walmart",
    "KO": "Coca-Cola", "PEP": "PepsiCo", "XOM": "Exxon", "PFE": "Pfizer",
    "BTC": "Bitcoin", "ETH": "Ethereum", "DOGE": "Dogecoin", "SOL": "Solana",
}

# Domains that are never financial journalism. Hit on real AAPL results.
JUNK_DOMAINS = {
    "pypi.org", "github.com", "gitlab.com", "npmjs.com", "stackoverflow.com",
    "reddit.com", "youtube.com", "facebook.com", "instagram.com",
    "ticker-line.com", "producthunt.com", "news.ycombinator.com",
}

# Words that only appear in market coverage. An article needs at least one of
# these somewhere, or it is product journalism that happens to name the company.
# This is what separates "Apple Q3 earnings beat" from "Apple Watch Ultra 4
# leaks" - both mention Apple in the title, only one is about the stock.
STRONG_FINANCE = {
    "stock", "stocks", "shares", "shareholder", "shareholders", "earnings",
    "revenue", "profit", "profits", "dividend", "valuation", "guidance",
    "analyst", "analysts", "investor", "investors", "quarter", "quarterly",
    "forecast", "upgrade", "downgrade", "nasdaq", "dow", "sp500", "index",
    "trading", "traded", "rally", "selloff", "bearish", "bullish", "ipo",
    "marketcap", "capitalization", "portfolio", "equity", "equities",
    "premarket", "yield", "eps", "buyback", "wall",
}

# Ambiguous on their own - "price" and "buy" show up in gadget reviews just as
# often as in market coverage, so they add rank but never qualify an article.
WEAK_FINANCE = {
    "price", "buy", "sell", "market", "markets", "growth", "outlook", "beat",
    "miss", "surge", "plunge", "drop", "fall", "rise", "decline", "target",
    "valuable", "trillion", "billion",
}


def _company(ticker: str) -> str:
    """Company name for a ticker, falling back to the symbol itself."""
    return COMPANY_NAMES.get(ticker.upper(), ticker.upper())


def _query(ticker: str) -> str:
    """NewsAPI query. Name OR symbol, and require a finance word.

    Without the finance clause, "Apple" pulls in product reviews and recipes.
    """
    company = _company(ticker)
    subject = f'"{company}" OR {ticker.upper()}' if company != ticker.upper() else ticker.upper()
    return f"({subject}) AND (stock OR shares OR earnings OR market OR investors)"


def _domain(url: str) -> str:
    """Bare hostname, no scheme and no www."""
    host = url.split("//")[-1].split("/")[0].split("?")[0].lower()
    return host[4:] if host.startswith("www.") else host


def _is_junk(url: str) -> bool:
    return _domain(url) in JUNK_DOMAINS


def _norm_title(title: str) -> str:
    """Normalized form for dedup.

    Aggregators republish the same headline verbatim, so a single AAPL fetch
    returned the Synopsys story twice under two different sources.
    """
    return "".join(c for c in title.lower() if c.isalnum() or c.isspace()).strip()


def _mentions_subject(text: str, ticker: str) -> bool:
    """True if the text actually names the company or the ticker."""
    company = _company(ticker)
    # Only substring-match a real company name. For an unmapped ticker
    # _company() echoes the symbol back, and substring-matching that would
    # make "V" match "Vodafone".
    if company.upper() != ticker.upper() and company.lower() in text.lower():
        return True
    # Word-boundary check so "V" (Visa) does not match every capital V.
    return any(w.strip(".,:;!?()[]'\"").upper() == ticker.upper()
               for w in text.split())


def _words(text: str) -> set[str]:
    """Lowercased alphanumeric words, punctuation stripped."""
    return {"".join(c for c in w if c.isalnum()).lower() for w in text.split()}


def _has_market_signal(item: ContentItem) -> bool:
    """True if the article is about markets at all, not just the company.

    Checked against title and description together, because a headline like
    "Apple unseats Nvidia" carries the market context in the body.
    """
    return bool(_words(f"{item.title} {item.text}") & STRONG_FINANCE)


def _relevance(item: ContentItem, ticker: str) -> int:
    """Higher is better.

    Market signal in the title is weighted above a bare company mention: an
    earnings story must outrank a product leak that merely names the company.
    """
    score = 0
    if _mentions_subject(item.title, ticker):
        score += 6
    elif _mentions_subject(item.text, ticker):
        score += 2

    title_words = _words(item.title)
    score += 5 * len(title_words & STRONG_FINANCE)
    score += 2 * len(title_words & WEAK_FINANCE)
    score += len(_words(item.text) & STRONG_FINANCE)
    return score


def _filter_articles(items: list[ContentItem], ticker: str,
                     limit: int) -> list[ContentItem]:
    """Drop junk domains, require a real mention, dedup, then take the best.

    Order matters: cheap exclusions first, ranking last.
    """
    kept: list[ContentItem] = []
    seen: set[str] = set()
    for it in items:
        if not it.title or _is_junk(it.url):
            continue
        if not _mentions_subject(it.title, ticker) and \
                not _mentions_subject(it.text, ticker):
            continue
        # Must be about markets, not just about the company. Without this,
        # AAPL returns iPhone leaks and TSLA returns car reviews.
        if not _has_market_signal(it):
            continue
        key = _norm_title(it.title)
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(it)

    kept.sort(key=lambda i: _relevance(i, ticker), reverse=True)
    return kept[:limit]


async def _fetch_news(ticker: str) -> tuple[list[ContentItem], list[str]]:
    if not settings.newsapi_key:
        return [], ["NEWSAPI_KEY missing - skipped news"]

    since = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": _query(ticker),
        "from": since,
        "sortBy": "publishedAt",
        "language": "en",
        # Over-fetch the maximum page, then filter down to news_limit. One
        # request costs the same whether it returns 15 articles or 100, and
        # the filter is strict enough that a small page starves it: an early
        # AAPL run kept only 4 of 60.
        "pageSize": 100,
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

    raw = []
    for a in data.get("articles", []):
        raw.append(ContentItem(
            source=(a.get("source") or {}).get("name", "unknown"),
            kind="news",
            title=a.get("title") or "",
            text=(a.get("description") or "")[:1000],
            url=a.get("url") or "",
            published_at=a.get("publishedAt"),
        ))

    items = _filter_articles(raw, ticker, settings.news_limit)
    warnings = []
    if raw and not items:
        warnings.append("all news articles filtered out as irrelevant")
    log.info("news: %d fetched -> %d kept for %s", len(raw), len(items), ticker)
    return items, warnings


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
