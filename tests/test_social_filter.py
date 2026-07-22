"""
Tests for _filter_social() - relevance filtering for Reddit posts.

Why this is separate from the news filter: reddit.com is in JUNK_DOMAINS (it
belongs there, to keep Reddit links out of NEWS results) so _filter_articles
would drop every social item, and the market-signal rule would throw away
posts like "AAPL 7/16 Puts 0dte" that carry real sentiment with no formal
finance vocabulary.

Pure function: no network, no credentials.
"""
import pytest

from app.agents.collector import _filter_social
from app.schemas import ContentItem


def post(title, text="", popularity=0, sub="stocks"):
    return ContentItem(source=f"r/{sub}", kind="reddit", title=title,
                       text=text, url="https://reddit.com/r/x/1",
                       popularity=popularity)


# ---------- the subject must be in the title ----------

def test_keeps_posts_naming_the_ticker_in_the_title():
    assert len(_filter_social([post("AAPL 7/16 Puts 0dte")], "AAPL", 15)) == 1


def test_keeps_posts_naming_the_company_in_the_title():
    kept = _filter_social([post("Apple surpassed Nvidia in market value")],
                          "AAPL", 15)
    assert len(kept) == 1


def test_body_only_mention_is_rejected():
    """
    Regression from live data: a 684-upvote Goldman Sachs thread and "How to
    find stocks that go up instead of down?" both survived a TSLA search
    because each named Tesla once inside a long body. A passing name-drop is
    not sentiment about the stock.
    """
    items = [
        post("Goldman Sachs to Create Private Market Platform for Rich Investors",
             text="Also worth watching Tesla this week", popularity=684),
        post("How to find stocks that go up instead of down?",
             text="I looked at Tesla and a few others", popularity=0),
    ]
    assert _filter_social(items, "TSLA", 15) == []


def test_megathreads_drop_out_naturally():
    """A thread titled 'Daily Discussion' never names the ticker."""
    items = [
        post("r/Stocks Daily Discussion & Fundamentals Friday Jul 17, 2026", popularity=14),
        post("Weekly Earnings Thread 7/20 7/24", popularity=210),
    ]
    assert _filter_social(items, "AAPL", 15) == []


def test_unrelated_ticker_post_is_rejected():
    """An NVDA post is not AAPL sentiment."""
    items = [post("Metrics for the top 3 show NVDA is incredible at this price")]
    assert _filter_social(items, "AAPL", 15) == []
    assert len(_filter_social(items, "NVDA", 15)) == 1


# ---------- no market-signal requirement ----------

def test_slang_post_without_finance_vocabulary_survives():
    """
    The news filter would reject this - no strong finance word anywhere - but
    it is exactly the kind of retail sentiment the social source exists for.
    """
    items = [post("Regard play of the week: AAPL 7/16 Puts 0dte", popularity=32)]
    assert len(_filter_social(items, "AAPL", 15)) == 1


def test_reddit_urls_are_not_treated_as_junk():
    """reddit.com is in JUNK_DOMAINS for news; social must ignore that."""
    items = [post("TSLA to the moon")]
    assert len(_filter_social(items, "TSLA", 15)) == 1


# ---------- ordering and limits ----------

def test_ordered_by_upvotes():
    items = [post("AAPL dips", popularity=5),
             post("AAPL rips", popularity=900),
             post("AAPL flat", popularity=100)]
    kept = _filter_social(items, "AAPL", 15)
    assert [i.popularity for i in kept] == [900, 100, 5]


def test_limit_keeps_the_most_upvoted():
    items = [post(f"AAPL take {n}", popularity=n) for n in range(20)]
    kept = _filter_social(items, "AAPL", 3)
    assert [i.popularity for i in kept] == [19, 18, 17]


def test_duplicate_titles_collapse():
    items = [post("AAPL earnings thoughts", popularity=50),
             post("AAPL earnings thoughts", popularity=10)]
    assert len(_filter_social(items, "AAPL", 15)) == 1


def test_empty_and_untitled_input_is_safe():
    assert _filter_social([], "AAPL", 15) == []
    assert _filter_social([post("")], "AAPL", 15) == []


def test_lowercase_common_word_still_not_a_ticker():
    """Same case-sensitivity rule as news: 'shop' is not SHOP."""
    assert _filter_social([post("my local shop sells these")], "SHOP", 15) == []
    assert len(_filter_social([post("SHOP earnings tomorrow")], "SHOP", 15)) == 1
