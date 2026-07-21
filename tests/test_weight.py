"""
Tests for analyzer._weight() - source credibility weighting.

Why these exist: CLAUDE.md rule 4 says news outranks Reddit and one viral post
must never be able to flip the signal. The weight tiers are what enforce that,
so the cap gets a test of its own.

No API keys and no network needed - _weight is a pure function.
"""
import pytest

from app.agents.analyzer import _weight
from app.schemas import ContentItem


def news(title="headline"):
    return ContentItem(source="reuters", kind="news", title=title)


def reddit(popularity, title="post"):
    return ContentItem(source="r/stocks", kind="reddit", title=title,
                       popularity=popularity)


def weighted_mean(pairs):
    """Same aggregation the analyzer uses: sum(score*weight)/sum(weight)."""
    total_w = sum(w for _, w in pairs) or 1.0
    return sum(s * w for s, w in pairs) / total_w


# ---------- tiers ----------

def test_news_always_full_weight():
    """News is 1.0 regardless of popularity, which is always 0 for news."""
    assert _weight(news()) == 1.0
    assert _weight(ContentItem(source="reuters", kind="news",
                               title="x", popularity=99999)) == 1.0


@pytest.mark.parametrize("popularity,expected", [
    (0, 0.3),
    (100, 0.3),        # boundary: >100 required, not >=
    (101, 0.5),
    (1000, 0.5),       # boundary: >1000 required, not >=
    (1001, 0.7),
    (50000, 0.7),
])
def test_reddit_popularity_tiers(popularity, expected):
    assert _weight(reddit(popularity)) == expected


def test_reddit_weight_is_capped_below_news():
    """Even a megaviral post must count for less than a single news article."""
    assert _weight(reddit(10_000_000)) < _weight(news())


def test_weight_is_monotonic_in_popularity():
    weights = [_weight(reddit(p)) for p in (0, 50, 100, 500, 1000, 5000)]
    assert weights == sorted(weights)


# ---------- CLAUDE.md rule 4: one viral post cannot flip the signal ----------

def test_single_viral_post_cannot_flip_against_news_consensus():
    """
    Three bullish news articles vs one maximally-viral, maximally-bearish post.
    The aggregate must stay bullish (>0.15), or rule 4 is violated.
    """
    items = [(0.6, _weight(news())) for _ in range(3)]
    items.append((-1.0, _weight(reddit(10_000_000))))
    assert weighted_mean(items) > 0.15


def test_viral_post_still_moves_the_number():
    """Capped, not ignored - it should pull the score down without flipping it."""
    without = weighted_mean([(0.6, _weight(news())) for _ in range(3)])
    with_post = weighted_mean([(0.6, _weight(news())) for _ in range(3)]
                              + [(-1.0, _weight(reddit(10_000_000)))])
    assert with_post < without


def test_reddit_consensus_can_still_win():
    """Weighting must not silence Reddit entirely when many posts agree."""
    items = [(-0.8, _weight(reddit(5000))) for _ in range(10)]
    items.append((0.5, _weight(news())))
    assert weighted_mean(items) < -0.15
