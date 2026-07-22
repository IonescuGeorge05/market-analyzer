"""
AGENT 1 - COLLECTOR

No LLM. Pure data gathering. Hits three sources concurrently and
normalizes everything into one CollectedData object.

Design rule: this agent never fails hard. If one source is down,
it records a warning and returns what it has.
"""
import asyncio
import logging
import xml.etree.ElementTree as ET
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
# An unmapped ticker searches the bare symbol, which matches far less prose:
# PLTR returned 15 candidate articles where AAPL returned 99, and RIVN
# returned so few that it fell under the 5-item thin-evidence threshold. So
# the map is worth keeping wide. Retail-popular names matter as much as the
# index heavyweights here, because they are what people actually look up.
COMPANY_NAMES = {
    # Big tech
    "AAPL": "Apple", "MSFT": "Microsoft", "GOOGL": "Alphabet", "GOOG": "Alphabet",
    "AMZN": "Amazon", "TSLA": "Tesla", "META": "Meta", "NVDA": "Nvidia",
    "NFLX": "Netflix", "AMD": "AMD", "INTC": "Intel", "IBM": "IBM",
    "ORCL": "Oracle", "CRM": "Salesforce", "ADBE": "Adobe", "CSCO": "Cisco",
    "QCOM": "Qualcomm", "TXN": "Texas Instruments", "AVGO": "Broadcom",
    "MU": "Micron", "ARM": "Arm Holdings", "TSM": "TSMC", "SMCI": "Supermicro",
    # Retail-popular / high-chatter
    "PLTR": "Palantir", "RIVN": "Rivian", "LCID": "Lucid", "NIO": "Nio",
    "COIN": "Coinbase", "HOOD": "Robinhood", "SOFI": "SoFi",
    "MSTR": "MicroStrategy", "GME": "GameStop", "AMC": "AMC Entertainment",
    "SHOP": "Shopify", "SQ": "Block", "PYPL": "PayPal", "SNAP": "Snap",
    "PINS": "Pinterest", "RBLX": "Roblox", "U": "Unity", "SPOT": "Spotify",
    "ABNB": "Airbnb", "DASH": "DoorDash", "UBER": "Uber", "LYFT": "Lyft",
    "ZM": "Zoom", "CRWD": "CrowdStrike", "SNOW": "Snowflake", "NET": "Cloudflare",
    "DDOG": "Datadog", "MDB": "MongoDB", "TTD": "The Trade Desk",
    # Industrials, finance, consumer, energy, health
    "BA": "Boeing", "GE": "General Electric", "CAT": "Caterpillar",
    "F": "Ford", "GM": "General Motors", "DIS": "Disney", "NKE": "Nike",
    "SBUX": "Starbucks", "MCD": "McDonald's", "KO": "Coca-Cola",
    "PEP": "PepsiCo", "WMT": "Walmart", "COST": "Costco", "TGT": "Target",
    "HD": "Home Depot", "JPM": "JPMorgan", "BAC": "Bank of America",
    "GS": "Goldman Sachs", "MS": "Morgan Stanley", "WFC": "Wells Fargo",
    "V": "Visa", "MA": "Mastercard", "BRK.B": "Berkshire Hathaway",
    "XOM": "Exxon", "CVX": "Chevron", "PFE": "Pfizer", "JNJ": "Johnson & Johnson",
    "MRNA": "Moderna", "LLY": "Eli Lilly", "UNH": "UnitedHealth",
    # Crypto
    "BTC": "Bitcoin", "ETH": "Ethereum", "DOGE": "Dogecoin", "SOL": "Solana",
    "XRP": "XRP", "ADA": "Cardano", "AVAX": "Avalanche", "LINK": "Chainlink",
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


# Symbols that are also ordinary English words. NewsAPI matches
# case-insensitively, so putting one of these in the query floods the results:
# `("Shopify" OR SHOP)` returned 98 articles about shopping and travel, not one
# of which mentioned Shopify. For these, search the company name alone.
AMBIGUOUS_SYMBOLS = {
    "SHOP", "COIN", "CAT", "NET", "ARM", "LINK", "ALL", "ON", "IT", "CAR",
    "GO", "KEY", "NOW", "A", "U", "F", "V", "MA", "MS", "GE", "HD", "SQ",
    "SOL", "ADA", "TGT", "DIS", "BA", "GM", "MU", "PINS", "SNAP", "ZM",
}


def _query(ticker: str) -> str:
    """NewsAPI query: company name, plus the symbol when the symbol is safe.

    The symbol adds real recall for a distinctive ticker like AAPL, but it is
    poison for one that doubles as a word - see AMBIGUOUS_SYMBOLS. Dropping it
    for everything cost AAPL a third of its results, so the rule is targeted
    rather than blanket.

    The finance clause is what stops "Apple" pulling in recipes and reviews.
    """
    symbol = ticker.upper()
    company = _company(ticker)
    if company.upper() == symbol:            # unmapped: symbol is all we have
        subject = symbol
    elif symbol in AMBIGUOUS_SYMBOLS:        # name only, symbol would flood
        subject = f'"{company}"'
    else:
        subject = f'"{company}" OR {symbol}'
    return f"({subject}) AND (stock OR shares OR earnings OR market OR investors)"


def _domain(url: str) -> str:
    """Bare hostname, no scheme and no www."""
    host = url.split("//")[-1].split("/")[0].split("?")[0].lower()
    return host[4:] if host.startswith("www.") else host


def _is_junk(url: str) -> bool:
    return _domain(url) in JUNK_DOMAINS


def _norm_title(title: str) -> str:
    """Normalized form for exact dedup.

    Aggregators republish the same headline verbatim, so a single AAPL fetch
    returned the Synopsys story twice under two different sources.
    """
    return "".join(c for c in title.lower() if c.isalnum() or c.isspace()).strip()


# Words that carry no topic. Dropped before comparing headlines so that two
# stories are judged on their nouns, not on how many "the"s they share.
_TITLE_STOP = {
    "a", "an", "the", "to", "of", "for", "and", "or", "in", "on", "at", "is",
    "are", "was", "were", "with", "by", "as", "its", "it", "this", "that",
    "from", "after", "before", "what", "how", "why", "will", "be", "been",
    "has", "have", "had", "who", "you", "your", "could", "would", "may",
    "says", "say", "said", "report", "reports", "new", "than", "into", "out",
}

# Jaccard above this means "the same story, reworded". Tuned on a live AAPL
# result where five outlets carried one leasing announcement: real duplicate
# pairs scored 0.38 to 1.00 while the closest unrelated pair scored 0.23, so
# 0.35 sits in the gap. The overlap coefficient was tried first and rejected:
# unrelated headlines reached 0.60 with it, overlapping the duplicate range.
NEAR_DUP_THRESHOLD = 0.35


def _title_tokens(title: str) -> set[str]:
    """Content words of a headline, lowercased, stopwords and noise removed."""
    cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in title.lower())
    return {w for w in cleaned.split() if len(w) > 2 and w not in _TITLE_STOP}


def _similarity(a: str, b: str) -> float:
    """Jaccard overlap of two headlines' content words, 0.0 to 1.0."""
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _drop_near_duplicates(items: list[ContentItem]) -> list[ContentItem]:
    """Collapse retellings of one story, keeping the first of each cluster.

    Exact-title dedup is not enough. One Apple leasing announcement arrived
    from five outlets under five different headlines, each scored +0.60, and
    all five counted toward the weighted average - inflating it by roughly
    18% off a single news event. The aggregate is supposed to reflect how
    many independent things are happening, not how many outlets rewrote the
    same wire copy.

    Callers must sort by relevance first: this keeps the earliest member of
    each cluster, so the best-ranked retelling is the one that survives.
    """
    kept: list[ContentItem] = []
    for it in items:
        if any(_similarity(it.title, k.title) >= NEAR_DUP_THRESHOLD for k in kept):
            continue
        kept.append(it)
    return kept


def _mentions_subject(text: str, ticker: str) -> bool:
    """True if the text actually names the company or the ticker.

    The ticker comparison is deliberately CASE-SENSITIVE. Real coverage writes
    symbols in caps ("SHOP climbs 4%", "$AAPL"), while the same letters in
    lowercase are usually just an English word. Matching case-insensitively
    made ticker SHOP (Shopify) pull in an article about a dosa shop owner, and
    COIN (Coinbase) pull in a Zcash story. Tickers that collide with common
    words - SHOP, ALL, ON, IT, CAR, GO, KEY, NOW - all had this problem.
    """
    company = _company(ticker)
    # Only substring-match a real company name. For an unmapped ticker
    # _company() echoes the symbol back, and substring-matching that would
    # make "V" match "Vodafone".
    if company.upper() != ticker.upper() and company.lower() in text.lower():
        return True
    symbol = ticker.upper()
    # Strip $ too, so the "$AAPL" cashtag convention still matches.
    return any(w.strip(".,:;!?()[]'\"$") == symbol for w in text.split())


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

    # Rank first, then collapse retellings, so the best-worded version of a
    # story is the one kept. Limit last, so dropped duplicates free up slots
    # for genuinely different stories rather than shrinking the result.
    kept.sort(key=lambda i: _relevance(i, ticker), reverse=True)
    return _drop_near_duplicates(kept)[:limit]


def _filter_social(items: list[ContentItem], ticker: str,
                   limit: int) -> list[ContentItem]:
    """Relevance filter for social posts. Deliberately lighter than the news one.

    Social posts cannot use _filter_articles: reddit.com is in JUNK_DOMAINS
    (it belongs there, to keep Reddit links out of NEWS results), and the
    market-signal rule would throw away posts like "AAPL 7/16 Puts 0dte" that
    carry real sentiment without a single formal finance word.

    What social does need is the subject check. PRAW's search returns loosely
    related posts: a TSLA search returned a Goldman Sachs story, a weekly
    earnings megathread and "How to find stocks that go up instead of down?",
    none of which mention Tesla. Megathreads drop out naturally here, since a
    thread titled "Daily Discussion" never names the ticker.

    The subject must appear in the TITLE, which is stricter than the news
    filter allows. Accepting body mentions let a 684-upvote Goldman Sachs
    thread and "How to find stocks that go up instead of down?" through on a
    TSLA search, because each named Tesla once somewhere in a long post. A
    passing name-drop is not sentiment about the stock, and the news sources
    already carry the volume.
    """
    kept: list[ContentItem] = []
    seen: set[str] = set()
    for it in items:
        if not it.title or not _mentions_subject(it.title, ticker):
            continue
        key = _norm_title(it.title)
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(it)

    # Upvotes order what survives, matching how _weight() values popularity.
    kept.sort(key=lambda i: i.popularity, reverse=True)
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

    # Returned unfiltered on purpose: run() filters the combined pool from
    # every news source at once, so dedup catches the same story arriving
    # from NewsAPI and Yahoo under two different publisher names.
    return raw, []


# Yahoo returns 404 to a client with no User-Agent, so send a normal one.
# This is not evasion - the feed is public and meant to be read by feed
# readers, it just refuses obviously-empty requests.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline"


async def _fetch_yahoo(ticker: str) -> tuple[list[ContentItem], list[str]]:
    """Yahoo Finance's per-ticker RSS feed. No API key, no quota.

    Exists because NewsAPI's free tier is thin for anything outside the
    megacaps: SHOP kept 0 articles and RIVN only 5, both under the 5-item
    threshold that costs confidence. Yahoo returns 20 headlines for every
    ticker tried, including those two.

    The feed is only loosely ticker-scoped - an AAPL request returned a
    seed-funding story about an unrelated startup - so these go through
    exactly the same relevance filter as NewsAPI results.
    """
    params = {"s": ticker.upper(), "region": "US", "lang": "en-US"}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(YAHOO_RSS, params=params,
                                 headers={"User-Agent": _UA})
            r.raise_for_status()
            return _parse_yahoo_rss(r.content), []
    except Exception as e:
        log.warning("yahoo fetch failed: %s", e)
        return [], [f"yahoo finance unavailable: {e}"]


def _parse_yahoo_rss(xml_bytes: bytes) -> list[ContentItem]:
    """RSS 2.0 -> ContentItem. Split out from the fetch so it is testable.

    Raises on malformed XML; the caller turns that into a warning.
    """
    root = ET.fromstring(xml_bytes)
    items = []
    for node in root.findall(".//item"):
        title = (node.findtext("title") or "").strip()
        if not title:
            continue
        items.append(ContentItem(
            source="Yahoo Finance",
            kind="news",
            title=title,
            text=(node.findtext("description") or "").strip()[:1000],
            url=(node.findtext("link") or "").strip(),
            published_at=(node.findtext("pubDate") or "").strip() or None,
        ))
    return items


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
    news, yahoo, reddit, price = await asyncio.gather(
        _fetch_news(ticker), _fetch_yahoo(ticker),
        _fetch_reddit(ticker), _fetch_price(ticker)
    )

    # Filter every news source together rather than per-source. The same
    # story reaches us from NewsAPI and Yahoo under different publisher
    # names, and dedup only catches that if it sees both at once.
    raw_news = news[0] + yahoo[0]
    articles = _filter_articles(raw_news, ticker, settings.news_limit)
    log.info("news: %d fetched (%d newsapi + %d yahoo) -> %d kept for %s",
             len(raw_news), len(news[0]), len(yahoo[0]), len(articles), ticker)

    social = _filter_social(reddit[0], ticker, settings.reddit_limit)
    log.info("social: %d fetched -> %d kept for %s",
             len(reddit[0]), len(social), ticker)

    items = articles + social
    warnings = news[1] + yahoo[1] + reddit[1] + price[1]
    if raw_news and not articles:
        warnings.append("all news articles filtered out as irrelevant")
    if not items:
        warnings.append("no content collected - check API keys")

    return CollectedData(ticker=ticker, items=items, price=price[0],
                         warnings=warnings)
