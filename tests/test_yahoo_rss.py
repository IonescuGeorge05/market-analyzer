"""
Tests for the Yahoo Finance RSS parsing.

Why this source exists: NewsAPI's free tier is thin outside the megacaps.
SHOP kept 0 articles and RIVN only 5 - both under the 5-item threshold that
costs confidence in _signal(). Yahoo returns 20 headlines for every ticker
tried, with no key and no quota.

_parse_yahoo_rss is split out from the fetch so it can be tested against
fixture XML with no network.
"""
import pytest

from app.agents.collector import _filter_articles, _parse_yahoo_rss

# Trimmed from a real AAPL response, including the unrelated startup story
# Yahoo actually returned in that feed.
FEED = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<rss version="2.0">
  <channel>
    <title>Yahoo Finance</title>
    <item>
      <title>AAPL Plans Major MacBook Overhaul To Capitalize On AI Demand</title>
      <description>Apple shares rose as analysts raised estimates.</description>
      <link>https://finance.yahoo.com/news/aapl-plans-major-macbook</link>
      <pubDate>Wed, 22 Jul 2026 16:00:00 +0000</pubDate>
      <guid>e62b7fcd-4654-30dc-88d7-92701477c2e4</guid>
    </item>
    <item>
      <title>Cheiron Raises $8M Seed Led by Menlo Ventures</title>
      <description>An unrelated startup funding round.</description>
      <link>https://finance.yahoo.com/healthcare/articles/cheiron-raises-8m</link>
      <pubDate>Wed, 22 Jul 2026 15:00:00 +0000</pubDate>
    </item>
    <item>
      <title></title>
      <description>An entry with no headline at all.</description>
      <link>https://finance.yahoo.com/news/empty</link>
    </item>
  </channel>
</rss>
"""


def test_parses_every_titled_item():
    items = _parse_yahoo_rss(FEED)
    assert len(items) == 2          # the untitled entry is skipped


def test_maps_rss_fields_onto_contentitem():
    first = _parse_yahoo_rss(FEED)[0]
    assert first.title.startswith("AAPL Plans Major MacBook")
    assert first.text == "Apple shares rose as analysts raised estimates."
    assert first.url.startswith("https://finance.yahoo.com/")
    assert first.published_at == "Wed, 22 Jul 2026 16:00:00 +0000"


def test_items_are_tagged_as_news():
    """Yahoo is journalism, so it must carry the full 1.0 news weight."""
    for it in _parse_yahoo_rss(FEED):
        assert it.kind == "news"
        assert it.source == "Yahoo Finance"


def test_untitled_entries_are_skipped():
    assert all(i.title for i in _parse_yahoo_rss(FEED))


def test_empty_feed_is_not_an_error():
    empty = b'<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
    assert _parse_yahoo_rss(empty) == []


def test_malformed_xml_raises_for_the_caller_to_catch():
    """_fetch_yahoo turns this into a warning rather than failing the run."""
    with pytest.raises(Exception):
        _parse_yahoo_rss(b"<rss><channel><item><title>unclosed")


def test_wellformed_non_rss_yields_nothing_rather_than_raising():
    """
    Yahoo answers a bad request with an HTML error page. When that page
    happens to be well-formed, parsing succeeds and simply finds no <item>
    nodes - which is the right outcome: no articles, no exception.
    """
    assert _parse_yahoo_rss(b"<html>404 not found</html>") == []


# ---------- the reason it goes through the shared filter ----------

def test_yahoo_items_are_still_subject_to_relevance_filtering():
    """
    The feed is only loosely ticker-scoped: a real AAPL request returned a
    seed-funding story about an unrelated healthcare startup. Yahoo results
    get no free pass.
    """
    kept = _filter_articles(_parse_yahoo_rss(FEED), "AAPL", 10)
    assert [i.title for i in kept] == [
        "AAPL Plans Major MacBook Overhaul To Capitalize On AI Demand"
    ]


def test_duplicate_story_across_sources_is_collapsed():
    """
    The point of filtering the combined pool rather than per source: the same
    story arrives from NewsAPI and Yahoo under different publisher names.
    """
    from app.schemas import ContentItem
    yahoo = _parse_yahoo_rss(FEED)[0]
    newsapi = ContentItem(
        source="Reuters", kind="news", title=yahoo.title,
        text="Apple shares rose.", url="https://reuters.com/x",
    )
    assert len(_filter_articles([yahoo, newsapi], "AAPL", 10)) == 1
