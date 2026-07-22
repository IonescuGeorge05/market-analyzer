"""
Run history.

Every analysis is written here so the signal can be evaluated later against
what prices actually did. Without a record of past signals there is nothing
to measure, and an unmeasured signal is just an opinion with a number on it.

Lives outside app/agents/ because the pipeline writes it and the API reads
it, and design rule 1 forbids cross-agent imports.

Stdlib sqlite3 on purpose: no new dependency, no server, and the file is
easy to inspect or hand to a notebook.

Design rule 3 applies here too. A failed write must never break a run - the
user asked for an analysis, not for bookkeeping - so save() swallows and logs
rather than raising.
"""
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.schemas import Report

log = logging.getLogger(__name__)

DB_PATH = Path("data/runs.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    -- The decision timestamp, UTC. Everything downstream depends on this
    -- being the moment the signal existed: forward returns may only use
    -- prices strictly after it, or the evaluation is looking ahead.
    created_at      TEXT    NOT NULL,
    signal          TEXT    NOT NULL,
    confidence      INTEGER NOT NULL,
    overall_score   REAL    NOT NULL,
    classification  TEXT    NOT NULL,
    item_count      INTEGER NOT NULL,
    -- 'llm', 'vader', or 'mixed' when a batch fell back. Runs scored by
    -- VADER should be excluded from evaluation rather than silently mixed in.
    scorer          TEXT    NOT NULL,
    -- Point-in-time price. Recorded now because looking it up later risks
    -- getting a revised or split-adjusted number that was never observable.
    price_at_signal REAL,
    price_trend     TEXT,
    warnings        TEXT    NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_runs_ticker_time ON runs (ticker, created_at);
"""


@contextmanager
def _connect(path: Path | None = None):
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _scorer_of(report: Report) -> str:
    """One label for the run: llm, vader, mixed, or none."""
    kinds = {s.scorer for s in report.scored_items}
    if not kinds:
        return "none"
    if len(kinds) == 1:
        return kinds.pop()
    return "mixed"


def save(report: Report, path: Path | None = None) -> int | None:
    """Record one analysis. Returns the row id, or None if the write failed."""
    try:
        with _connect(path) as conn:
            cur = conn.execute(
                """INSERT INTO runs (ticker, created_at, signal, confidence,
                       overall_score, classification, item_count, scorer,
                       price_at_signal, price_trend, warnings)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    report.ticker,
                    datetime.now(timezone.utc).isoformat(),
                    report.signal,
                    report.confidence,
                    report.overall_score,
                    report.classification,
                    report.item_count,
                    _scorer_of(report),
                    report.price.current,
                    report.price.trend,
                    json.dumps(report.warnings),
                ),
            )
            return cur.lastrowid
    except Exception as e:
        # Never fail a run over bookkeeping.
        log.warning("could not record run for %s: %s", report.ticker, e)
        return None


def history(ticker: str | None = None, limit: int = 100,
            path: Path | None = None) -> list[dict]:
    """Past runs, newest first. All tickers when ticker is None."""
    try:
        with _connect(path) as conn:
            if ticker:
                rows = conn.execute(
                    "SELECT * FROM runs WHERE ticker = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (ticker.upper(), limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["warnings"] = json.loads(d["warnings"] or "[]")
            out.append(d)
        return out
    except Exception as e:
        log.warning("could not read run history: %s", e)
        return []


def stats(path: Path | None = None) -> dict:
    """Summary of what has been recorded so far."""
    try:
        with _connect(path) as conn:
            total = conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"]
            tickers = conn.execute(
                "SELECT COUNT(DISTINCT ticker) c FROM runs").fetchone()["c"]
            span = conn.execute(
                "SELECT MIN(created_at) a, MAX(created_at) b FROM runs").fetchone()
            by_signal = {
                r["signal"]: r["c"] for r in conn.execute(
                    "SELECT signal, COUNT(*) c FROM runs GROUP BY signal")
            }
            llm_only = conn.execute(
                "SELECT COUNT(*) c FROM runs WHERE scorer = 'llm'").fetchone()["c"]
        return {
            "total_runs": total,
            "distinct_tickers": tickers,
            "first_run": span["a"],
            "latest_run": span["b"],
            "by_signal": by_signal,
            "llm_scored_runs": llm_only,
        }
    except Exception as e:
        log.warning("could not read stats: %s", e)
        return {"total_runs": 0, "distinct_tickers": 0, "first_run": None,
                "latest_run": None, "by_signal": {}, "llm_scored_runs": 0}
