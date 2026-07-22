"""
Tests for the run store.

Why this exists: the signal cannot be evaluated against forward returns
unless every run is recorded with the price at the moment it was made. This
is the foundation the whole quant direction sits on, so the record has to be
reliable - and, per design rule 3, a failed write must never break a run.

Each test gets its own temp database via tmp_path, so nothing touches the
real data/runs.db.
"""
import pytest

from app import store
from app.schemas import PriceData, Report, ScoredItem


def report(ticker="AAPL", signal="BUY", scorers=("llm",), **kw):
    items = [ScoredItem(source="reuters", kind="news", title=f"{ticker} news",
                        score=0.5, weight=1.0, scorer=s) for s in scorers]
    return Report(
        ticker=ticker, signal=signal, confidence=kw.get("confidence", 70),
        overall_score=kw.get("overall_score", 0.3), classification="Bullish",
        summary="x", item_count=len(items), scored_items=items,
        price=kw.get("price", PriceData(current=200.0, trend="up")),
        warnings=kw.get("warnings", []),
    )


@pytest.fixture
def db(tmp_path):
    return tmp_path / "runs.db"


def test_save_returns_a_row_id(db):
    assert store.save(report(), path=db) == 1


def test_saved_run_round_trips(db):
    store.save(report(ticker="NVDA", signal="SELL"), path=db)
    rows = store.history(path=db)
    assert len(rows) == 1
    r = rows[0]
    assert r["ticker"] == "NVDA"
    assert r["signal"] == "SELL"
    assert r["price_at_signal"] == 200.0
    assert r["price_trend"] == "up"


def test_created_at_is_recorded(db):
    """The decision timestamp is what forward-return evaluation keys on."""
    store.save(report(), path=db)
    assert store.history(path=db)[0]["created_at"]


def test_warnings_are_stored_as_json(db):
    store.save(report(warnings=["reddit unavailable", "price empty"]), path=db)
    assert store.history(path=db)[0]["warnings"] == ["reddit unavailable", "price empty"]


# ---------- scorer labelling: evaluation must exclude VADER runs ----------

def test_all_llm_run_labelled_llm(db):
    store.save(report(scorers=("llm", "llm", "llm")), path=db)
    assert store.history(path=db)[0]["scorer"] == "llm"


def test_all_vader_run_labelled_vader(db):
    store.save(report(scorers=("vader", "vader")), path=db)
    assert store.history(path=db)[0]["scorer"] == "vader"


def test_partial_fallback_labelled_mixed(db):
    """A run where one batch fell back must be distinguishable, not hidden."""
    store.save(report(scorers=("llm", "llm", "vader")), path=db)
    assert store.history(path=db)[0]["scorer"] == "mixed"


# ---------- history queries ----------

def test_history_is_newest_first(db):
    for t in ("AAPL", "NVDA", "TSLA"):
        store.save(report(ticker=t), path=db)
    tickers = [r["ticker"] for r in store.history(path=db)]
    assert tickers == ["TSLA", "NVDA", "AAPL"]


def test_history_filters_by_ticker(db):
    store.save(report(ticker="AAPL"), path=db)
    store.save(report(ticker="NVDA"), path=db)
    store.save(report(ticker="AAPL"), path=db)
    rows = store.history(ticker="AAPL", path=db)
    assert len(rows) == 2
    assert all(r["ticker"] == "AAPL" for r in rows)


def test_history_respects_limit(db):
    for _ in range(10):
        store.save(report(), path=db)
    assert len(store.history(limit=3, path=db)) == 3


def test_history_of_empty_db_is_empty(db):
    assert store.history(path=db) == []


# ---------- stats ----------

def test_stats_counts_and_groups(db):
    store.save(report(ticker="AAPL", signal="BUY"), path=db)
    store.save(report(ticker="AAPL", signal="BUY"), path=db)
    store.save(report(ticker="NVDA", signal="HOLD"), path=db)
    s = store.stats(path=db)
    assert s["total_runs"] == 3
    assert s["distinct_tickers"] == 2
    assert s["by_signal"] == {"BUY": 2, "HOLD": 1}


def test_stats_counts_llm_scored_runs(db):
    store.save(report(scorers=("llm",)), path=db)
    store.save(report(scorers=("vader",)), path=db)
    store.save(report(scorers=("llm", "vader")), path=db)   # mixed
    assert store.stats(path=db)["llm_scored_runs"] == 1


# ---------- design rule 3: bookkeeping must never break a run ----------

def test_save_never_raises_on_a_bad_path(monkeypatch):
    """A write failure returns None instead of propagating."""
    import sqlite3
    monkeypatch.setattr(store, "_connect",
                        lambda *a, **k: (_ for _ in ()).throw(sqlite3.Error("disk full")))
    assert store.save(report()) is None


def test_missing_price_is_stored_as_null(db):
    store.save(report(price=PriceData()), path=db)
    assert store.history(path=db)[0]["price_at_signal"] is None
