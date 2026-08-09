"""
Tests for forward-return evaluation.

The three things worth guarding here are the three standard ways to fool
yourself when scoring your own signal:

  1. entry must never be a price the decision could not have traded on
     (look-ahead),
  2. HOLD must never be counted as a directional win (inflated hit rate),
  3. a thin sample must never be reported as if it concluded something.

Everything below runs on handed-in price series, so no network and no keys.
"""
from datetime import date, datetime, timezone

import pytest

from app.evaluate import (
    MIN_SAMPLES, confidence_bucket, direction_correct, entry_index,
    evaluate_runs, forward_return, price_drift, summarize,
)

# Ten consecutive sessions. Weekends are irrelevant here - the evaluator
# counts SESSIONS, not calendar days, which is exactly why it indexes into a
# list of real trading dates rather than doing date arithmetic.
SESSIONS = [date(2026, 8, d) for d in (3, 4, 5, 6, 7, 10, 11, 12, 13, 14)]
CLOSES = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 110.0]


def run(ticker="AAPL", signal="BUY", when="2026-08-03T14:00:00+00:00",
        confidence=70, scorer="llm", price=100.0):
    return {
        "ticker": ticker, "created_at": when, "signal": signal,
        "confidence": confidence, "scorer": scorer, "price_at_signal": price,
    }


def series(sessions=None, closes=None):
    return {"AAPL": (sessions or SESSIONS, closes or CLOSES)}


# --- forward_return ---------------------------------------------------------

def test_forward_return_is_a_percentage():
    assert forward_return(100.0, 110.0) == 10.0


def test_forward_return_is_signed():
    assert forward_return(100.0, 90.0) == -10.0


def test_forward_return_of_zero_entry_is_zero_not_a_crash():
    """A missing entry price must not raise inside a bookkeeping routine."""
    assert forward_return(0.0, 110.0) == 0.0


# --- direction_correct ------------------------------------------------------

def test_buy_is_correct_when_price_rises():
    assert direction_correct("BUY", 2.5) is True


def test_buy_is_wrong_when_price_falls():
    assert direction_correct("BUY", -2.5) is False


def test_sell_is_correct_when_price_falls():
    assert direction_correct("SELL", -2.5) is True


def test_sell_is_wrong_when_price_rises():
    assert direction_correct("SELL", 2.5) is False


def test_hold_scores_neither_way():
    """HOLD makes no directional claim, so it cannot be right or wrong.

    Counting HOLDs as wins is the cheapest way to manufacture a good hit rate.
    """
    assert direction_correct("HOLD", 5.0) is None
    assert direction_correct("HOLD", -5.0) is None


# --- confidence_bucket ------------------------------------------------------

@pytest.mark.parametrize("conf,expected", [
    (5, "<60"), (59, "<60"), (60, "60-74"), (74, "60-74"), (75, "75+"), (95, "75+"),
])
def test_confidence_buckets_at_their_boundaries(conf, expected):
    assert confidence_bucket(conf) == expected


# --- entry_index: the look-ahead guard -------------------------------------

def test_entry_is_the_session_after_the_decision_not_the_same_day():
    """The core anti-look-ahead rule.

    A signal made on the 3rd must not be credited with the 3rd's close - the
    store does not record whether the market was still open, so that close may
    never have been reachable.
    """
    decision = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
    assert entry_index(SESSIONS, decision) == 1        # the 4th, not the 3rd


def test_entry_skips_non_trading_days():
    """A Friday-evening signal enters on Monday's close, not on a weekend."""
    decision = datetime(2026, 8, 7, 21, 0, tzinfo=timezone.utc)   # Friday
    assert SESSIONS[entry_index(SESSIONS, decision)] == date(2026, 8, 10)


def test_entry_is_none_when_no_session_has_happened_since():
    decision = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    assert entry_index(SESSIONS, decision) is None


def test_a_decision_before_the_series_enters_on_the_first_session():
    decision = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    assert entry_index(SESSIONS, decision) == 0


# --- price_drift: the adjustment-basis cross-check --------------------------

def test_no_drift_when_the_recorded_price_matches_the_close():
    decision = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
    assert price_drift(100.0, SESSIONS, CLOSES, decision) == 0.0


def test_a_split_shows_up_as_large_drift():
    """After a 2:1 split, yfinance re-reports that session's close at half.

    The recorded price was right when it was written, so a big gap means the
    series moved underneath the record - the exact trap this module avoids by
    taking entry and exit from one request.
    """
    decision = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
    halved = [c / 2 for c in CLOSES]
    assert price_drift(100.0, SESSIONS, halved, decision) == -50.0


def test_drift_is_none_without_a_recorded_price():
    decision = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
    assert price_drift(None, SESSIONS, CLOSES, decision) is None


# --- evaluate_runs ----------------------------------------------------------

def test_a_resolved_run_gets_its_realized_return():
    evaluated, pending = evaluate_runs([run()], series(), horizon=5)
    assert not pending
    e = evaluated[0]
    assert e["entry_date"] == "2026-08-04"      # day after the decision
    assert e["exit_date"] == "2026-08-11"       # 5 sessions later
    assert e["entry_price"] == 101.0
    assert e["exit_price"] == 106.0
    assert e["correct"] is True


def test_a_run_whose_horizon_has_not_elapsed_is_pending_not_dropped():
    """Unresolved runs are reported. Dropping them would hide how much of the
    record is still undecided, which flatters the sample size."""
    recent = run(when="2026-08-12T14:00:00+00:00")
    evaluated, pending = evaluate_runs([recent], series(), horizon=5)
    assert not evaluated
    assert "horizon not elapsed" in pending[0]["reason"]


def test_a_run_with_no_price_history_is_pending():
    evaluated, pending = evaluate_runs([run(ticker="ZZZZ")], series(), horizon=5)
    assert not evaluated
    assert pending[0]["reason"] == "no price history"


def test_an_unparseable_timestamp_does_not_crash_the_batch():
    runs = [run(when="not-a-date"), run()]
    evaluated, pending = evaluate_runs(runs, series(), horizon=5)
    assert len(evaluated) == 1
    assert pending[0]["reason"] == "unparseable timestamp"


def test_a_falling_series_makes_sell_correct_and_buy_wrong():
    falling = list(reversed(CLOSES))
    evaluated, _ = evaluate_runs(
        [run(signal="SELL"), run(signal="BUY")], series(closes=falling), horizon=5)
    assert evaluated[0]["correct"] is True
    assert evaluated[1]["correct"] is False


# --- summarize --------------------------------------------------------------

def evaluated_row(signal="BUY", ret=1.0, correct=True, confidence=70):
    return {"ticker": "AAPL", "created_at": "2026-08-03T14:00:00+00:00",
            "signal": signal, "confidence": confidence, "scorer": "llm",
            "entry_date": "2026-08-04", "entry_price": 101.0,
            "exit_date": "2026-08-11", "exit_price": 106.0,
            "forward_return_pct": ret, "correct": correct,
            "price_drift_pct": 0.0}


def test_hit_rate_counts_only_directional_calls():
    """Two BUYs, one right, plus a HOLD that must not dilute or pad the rate."""
    rows = [evaluated_row(correct=True), evaluated_row(correct=False),
            evaluated_row(signal="HOLD", correct=None)]
    out = summarize(rows, [])
    assert out["directional_runs"] == 2
    assert out["hit_rate_pct"] == 50.0


def test_a_thin_sample_is_reported_but_marked_not_conclusive():
    out = summarize([evaluated_row()], [])
    assert out["conclusive"] is False
    assert out["hit_rate_pct"] == 100.0          # reported anyway...
    assert "NOT CONCLUSIVE" in out["caveats"][0]  # ...but never unqualified


def test_a_large_enough_sample_is_marked_conclusive():
    out = summarize([evaluated_row() for _ in range(MIN_SAMPLES)], [])
    assert out["conclusive"] is True
    assert not any("NOT CONCLUSIVE" in c for c in out["caveats"])


def test_costs_and_benchmark_caveat_is_always_present():
    """Even a conclusive hit rate is not a profit, and must say so."""
    out = summarize([evaluated_row() for _ in range(MIN_SAMPLES)], [])
    assert any("transaction costs" in c for c in out["caveats"])


def test_breakdown_by_signal():
    rows = [evaluated_row(signal="BUY", correct=True),
            evaluated_row(signal="SELL", ret=-2.0, correct=True)]
    out = summarize(rows, [])
    assert out["by_signal"]["BUY"]["n"] == 1
    assert out["by_signal"]["SELL"]["hit_rate_pct"] == 100.0


def test_breakdown_by_confidence_bucket():
    """The question worth asking: do high-confidence calls actually do better?"""
    rows = [evaluated_row(confidence=80, correct=True),
            evaluated_row(confidence=50, correct=False)]
    out = summarize(rows, [])
    assert out["by_confidence"]["75+"]["hit_rate_pct"] == 100.0
    assert out["by_confidence"]["<60"]["hit_rate_pct"] == 0.0


def test_pending_runs_are_surfaced_in_the_caveats():
    out = summarize([evaluated_row()], [{"reason": "horizon not elapsed"}])
    assert out["pending"] == 1
    assert any("have not resolved" in c for c in out["caveats"])


def test_price_drift_raises_a_data_integrity_caveat():
    row = evaluated_row()
    row["price_drift_pct"] = -50.0
    out = summarize([row], [])
    assert any("split" in c for c in out["caveats"])


def test_empty_input_summarizes_without_crashing():
    out = summarize([], [])
    assert out["evaluated"] == 0
    assert out["hit_rate_pct"] is None
    assert out["conclusive"] is False
