"""
Tests for price-series math.

_price_from_closes is the pure core shared by the yfinance and Alpha Vantage
paths: given daily closes oldest-first, produce current price, the 5-session
reference, the percent change and a trend with a 1% dead zone.
"""
import pytest

from app.agents.collector import _price_from_closes


def test_rising_series_reads_up():
    p = _price_from_closes([100, 101, 102, 103, 104, 110])
    assert p.current == 110.0
    assert p.week_ago == 100.0        # 5 sessions back
    assert p.change_pct == 10.0
    assert p.trend == "up"


def test_falling_series_reads_down():
    p = _price_from_closes([100, 99, 98, 97, 96, 90])
    assert p.trend == "down"
    assert p.change_pct == -10.0


def test_flat_within_one_percent_reads_flat():
    """Daily noise must not register as a move."""
    p = _price_from_closes([100, 100.2, 99.9, 100.1, 100.3, 100.5])
    assert p.trend == "flat"


def test_exactly_one_percent_is_still_flat():
    """The dead zone is exclusive: > 1% up, < -1% down."""
    p = _price_from_closes([100, 100, 100, 100, 100, 101])
    assert p.trend == "flat"


def test_reference_is_five_sessions_back_not_the_oldest():
    """A long series still compares against 5 sessions ago, not day one."""
    closes = [50] + [100, 101, 102, 103, 104, 105]   # the 50 is old noise
    p = _price_from_closes(closes)
    assert p.week_ago == 100.0        # not 50
    assert p.current == 105.0


def test_short_series_uses_oldest_available():
    p = _price_from_closes([100, 103])
    assert p.week_ago == 100.0
    assert p.current == 103.0


def test_single_close_has_no_change():
    p = _price_from_closes([100])
    assert p.current == 100.0
    assert p.change_pct == 0.0
    assert p.trend == "flat"


def test_empty_series_is_blank_pricedata():
    p = _price_from_closes([])
    assert p.current is None
    assert p.trend == "unknown"


def test_none_values_are_ignored():
    """yfinance can return NaN/None rows for non-trading days."""
    p = _price_from_closes([None, 100, 101, 102, 103, 104, 110])
    assert p.current == 110.0
    assert p.week_ago == 100.0
