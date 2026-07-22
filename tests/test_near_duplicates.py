"""
Tests for near-duplicate collapsing.

Why this exists: one Apple leasing announcement arrived from five outlets
under five different headlines. Exact-title dedup caught none of them, all
five scored +0.60, and all five counted toward the weighted average. The
sum was 6.0 over 15 items for +0.400; collapsing that one story gives 3.6
over 11 for +0.327 - roughly 18% of the signal coming from a single news
event. The aggregate is meant to reflect how many independent things are
happening, not how many outlets rewrote the same wire copy.

Threshold tuned on that live result: real duplicate pairs scored 0.38-1.00
Jaccard, the closest unrelated pair scored 0.23.
"""
import pytest

from app.agents.collector import (
    NEAR_DUP_THRESHOLD, _drop_near_duplicates, _filter_articles, _similarity,
    _title_tokens,
)
from app.schemas import ContentItem


def art(title, url="https://reuters.com/x", source="Reuters"):
    return ContentItem(source=source, kind="news", title=title,
                       text="Apple shares moved on the news, analysts said.", url=url)


# The five real headlines for one announcement.
LEASING = [
    "Apple to launch 'Upgrade' device leasing program to spur sales: report",
    "Apple to launch 'Apple Upgrade' device leasing program with Klarna on July 28th",
    "Apple to launch 'Upgrade' device leasing program to spur sales",
    "Apple is launching a Klarna-backed device leasing program called Apple Upgrade on July 28",
]

# Real headlines from the same fetch that are genuinely different stories.
DISTINCT = [
    "Bank of America doubles down on Apple stock ahead of earnings",
    "Alphabet vs Apple: The Better Buy Before Earnings",
    "Apple-OpenAI lawsuit won't derail IPO, but hangs over io Products",
    "Is Apple stock overvalued? Here's what AlphaSpace valuation metrics say.",
]


# ---------- tokenizing ----------

def test_stopwords_and_short_words_are_dropped():
    assert _title_tokens("The a to of Apple stock") == {"apple", "stock"}


def test_punctuation_does_not_split_topics():
    assert "klarna" in _title_tokens("Apple's Klarna-backed program")


# ---------- similarity ----------

def test_retellings_of_one_story_score_above_the_threshold():
    for i in range(len(LEASING)):
        for j in range(i + 1, len(LEASING)):
            s = _similarity(LEASING[i], LEASING[j])
            assert s >= NEAR_DUP_THRESHOLD, f"{s:.2f}: {LEASING[i]!r} vs {LEASING[j]!r}"


def test_genuinely_different_stories_score_below_the_threshold():
    for i in range(len(DISTINCT)):
        for j in range(i + 1, len(DISTINCT)):
            s = _similarity(DISTINCT[i], DISTINCT[j])
            assert s < NEAR_DUP_THRESHOLD, f"{s:.2f}: {DISTINCT[i]!r} vs {DISTINCT[j]!r}"


def test_identical_titles_score_one():
    assert _similarity("Apple beats earnings", "Apple beats earnings") == 1.0


def test_empty_titles_are_not_similar():
    assert _similarity("", "Apple beats earnings") == 0.0
    assert _similarity("", "") == 0.0


def test_similarity_is_symmetric():
    a, b = LEASING[0], LEASING[1]
    assert _similarity(a, b) == _similarity(b, a)


# ---------- collapsing ----------

def test_the_leasing_cluster_collapses_to_one():
    kept = _drop_near_duplicates([art(t) for t in LEASING])
    assert len(kept) == 1


def test_distinct_stories_all_survive():
    kept = _drop_near_duplicates([art(t) for t in DISTINCT])
    assert len(kept) == len(DISTINCT)


def test_first_of_a_cluster_is_the_one_kept():
    """Callers sort by relevance first, so the best-ranked retelling wins."""
    items = [art(t) for t in LEASING]
    assert _drop_near_duplicates(items)[0].title == LEASING[0]


def test_mixed_input_keeps_one_per_story():
    items = [art(t) for t in LEASING + DISTINCT]
    kept = _drop_near_duplicates(items)
    assert len(kept) == 1 + len(DISTINCT)


def test_empty_input_is_safe():
    assert _drop_near_duplicates([]) == []


# ---------- through the whole filter ----------

def test_full_filter_collapses_retellings_and_frees_slots():
    """
    Dedup runs before the limit, so a dropped duplicate makes room for a
    different story rather than shrinking the result.
    """
    items = [art(t) for t in LEASING + DISTINCT]
    kept = _filter_articles(items, "AAPL", 5)
    titles = [i.title for i in kept]
    assert len(kept) == 5
    assert sum(1 for t in titles if "leasing" in t) == 1


def test_aggregate_is_not_inflated_by_one_event():
    """
    The point of all this. Five copies of a +0.6 story alongside one -0.6
    story should not average out bullish.
    """
    from app.agents.analyzer import _weight
    items = _filter_articles(
        [art(t) for t in LEASING] + [art(DISTINCT[2])], "AAPL", 15
    )
    # One leasing item plus the lawsuit item, not five plus one.
    assert len(items) == 2
    scores = [0.6, -0.6]
    total_w = sum(_weight(i) for i in items)
    mean = sum(s * _weight(i) for s, i in zip(scores, items)) / total_w
    assert mean == pytest.approx(0.0, abs=1e-9)
