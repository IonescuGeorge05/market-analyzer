"""
Tests for reporter._signal() - the deterministic BUY/HOLD/SELL rule.

Why these exist: by design, the signal is computed in code, never by
the LLM, so the same numbers must always produce the same signal. That property
is only worth claiming if it is enforced by a test.

No API keys and no network needed - _signal is a pure function.
"""
import pytest

from app.agents.reporter import _signal
from app.schemas import PriceData, SentimentAnalysis


def make(score, *, items=10, trend="unknown", warnings=None):
    """Minimal SentimentAnalysis carrying only what _signal actually reads."""
    return SentimentAnalysis(
        ticker="TEST",
        overall_score=score,
        classification=("Bullish" if score > 0.15
                        else "Bearish" if score < -0.15 else "Neutral"),
        price=PriceData(trend=trend),
        item_count=items,
        warnings=warnings or [],
    )


# ---------- direction ----------

@pytest.mark.parametrize("score,expected", [
    (1.0, "BUY"),
    (0.5, "BUY"),
    (0.16, "BUY"),
    (0.15, "HOLD"),      # boundary is exclusive
    (0.0, "HOLD"),
    (-0.15, "HOLD"),     # boundary is exclusive
    (-0.16, "SELL"),
    (-0.5, "SELL"),
    (-1.0, "SELL"),
])
def test_direction_thresholds(score, expected):
    assert _signal(make(score))[0] == expected


def test_thresholds_are_exclusive_at_both_edges():
    """Exactly +/-0.15 must be HOLD, not BUY/SELL. Guards an off-by-one flip."""
    assert _signal(make(0.15))[0] == "HOLD"
    assert _signal(make(-0.15))[0] == "HOLD"


# ---------- determinism ----------

def test_same_input_gives_same_output():
    a = make(0.42, items=12, trend="up", warnings=["reddit unavailable"])
    first = _signal(a)
    assert all(_signal(a) == first for _ in range(50))


def test_direction_ignores_everything_except_score():
    """Price, volume and warnings may move confidence, never the direction."""
    base = _signal(make(0.4))[0]
    assert _signal(make(0.4, trend="down"))[0] == base
    assert _signal(make(0.4, items=1))[0] == base
    assert _signal(make(0.4, warnings=["x failed", "y unavailable"]))[0] == base


# ---------- confidence ----------

def test_confidence_always_within_bounds():
    """Schema declares confidence 0-100; _signal clamps to 5-95."""
    for score in (-1.0, -0.5, 0.0, 0.5, 1.0):
        for items in (0, 1, 5, 50):
            for trend in ("up", "down", "flat", "unknown"):
                for warns in ([], ["a failed", "b unavailable"]):
                    _, conf = _signal(make(score, items=items, trend=trend,
                                           warnings=warns))
                    assert 5 <= conf <= 95


def test_agreeing_price_trend_beats_contradicting_one():
    agree = _signal(make(0.5, trend="up"))[1]
    contradict = _signal(make(0.5, trend="down"))[1]
    neutral = _signal(make(0.5, trend="flat"))[1]
    assert agree > neutral > contradict


def test_sell_confirmed_by_falling_price():
    agree = _signal(make(-0.5, trend="down"))[1]
    contradict = _signal(make(-0.5, trend="up"))[1]
    assert agree > contradict


def test_thin_evidence_lowers_confidence():
    """By design, confidence drops when evidence is thin."""
    assert _signal(make(0.5, items=4))[1] < _signal(make(0.5, items=20))[1]


def test_missing_sources_lower_confidence():
    """A warning about a dead source must cost confidence."""
    clean = _signal(make(0.5, items=10))[1]
    degraded = _signal(make(0.5, items=10, warnings=["reddit unavailable"]))[1]
    assert degraded < clean


def test_stronger_score_raises_confidence():
    weak = _signal(make(0.2, items=10))[1]
    strong = _signal(make(0.9, items=10))[1]
    assert strong > weak


def test_worst_case_still_returns_valid_signal():
    """No evidence, contradicting price, dead sources - must not crash or clamp out."""
    signal, conf = _signal(make(0.2, items=0, trend="down",
                                warnings=["news failed", "reddit unavailable"]))
    assert signal == "BUY"
    assert 5 <= conf <= 95
