"""
FORWARD-RETURN EVALUATION

store.py records what the signal said. This module asks whether it was right.

A signal nobody scores is an opinion with a number on it. The point here is
not to demonstrate an edge - it is to build the machinery that could detect
one, and then report honestly what the machinery says, including when it says
"not enough data to tell". Refusing to draw a conclusion from 12 samples is
the whole skill.

Three things this is careful about, because each is a standard way to fool
yourself:

1. LOOK-AHEAD. Entry is the first close strictly after the decision DATE.
   store.py already refuses to look up the decision price after the fact;
   this is the other half of the same discipline.

2. ADJUSTMENT BASIS. Entry and exit closes always come from a single request
   per ticker, so both sit on the same split- and dividend-adjusted basis.
   Comparing a price recorded weeks ago against a freshly adjusted one books
   a fake -50% the first time a stock splits 2:1. The recorded price is used
   only as a cross-check, never as the return's base.

3. CONTAMINATED RUNS. Runs scored by VADER measure the fallback, not the
   model. They are excluded by default rather than silently averaged in.

Lives outside app/agents/ for the same reason store.py does: the API reads it
and it reads the store, and design rule 1 forbids cross-agent imports. It does
not import the Collector's price helpers either - it needs a dated series
rather than a 5-session summary, so it asks yfinance itself.

Not adjusted for transaction costs, slippage, or a market benchmark. A hit
rate here is a directional accuracy measurement, not a strategy return.
"""
import logging
from datetime import date, datetime, timedelta

from app import store

log = logging.getLogger(__name__)

# Trading sessions held between entry and exit.
HORIZON_SESSIONS = 5

# Below this many evaluated runs the numbers are still reported, but nothing
# is claimed from them. 30 is not a magic threshold that makes a result true;
# it is the point below which the noise so obviously dominates that quoting a
# hit rate would be misleading. Even above it, see the caveats in summarize().
MIN_SAMPLES = 30

# A recorded price this far from the same session's close today means the
# adjustment basis moved - almost always a split. Flagged, never patched.
PRICE_DRIFT_PCT = 5.0


# --- Pure functions: no network, no database, unit-tested directly ----------

def forward_return(entry: float, exit_price: float) -> float:
    """Percent change from entry close to exit close."""
    if not entry:
        return 0.0
    return round(((exit_price - entry) / entry) * 100, 4)


def direction_correct(signal: str, ret: float) -> bool | None:
    """Did the signal call the direction?

    None for HOLD. HOLD asserts nothing about direction, so scoring it as a
    hit or a miss would be inventing a claim the system never made - and
    counting HOLDs as wins is the easiest way to manufacture a good hit rate.
    """
    if signal == "BUY":
        return ret > 0
    if signal == "SELL":
        return ret < 0
    return None


def confidence_bucket(conf: int) -> str:
    """Coarse buckets. The interesting question is whether high-confidence
    calls actually do better than low-confidence ones - if they do not, the
    confidence formula in reporter._signal() is decoration."""
    if conf >= 75:
        return "75+"
    if conf >= 60:
        return "60-74"
    return "<60"


def entry_index(sessions: list[date], decision: datetime) -> int | None:
    """Index of the first close usable as an entry price, or None.

    Strictly after the decision DATE, not the decision timestamp. The store
    records when the signal existed but not whether the market was still open,
    so that day's close may or may not have been reachable. The next session's
    close is unambiguously observable after the decision under either reading.

    This costs roughly a day of return and removes the question entirely. When
    the alternative is silently inflating your own results, take the
    conservative direction.
    """
    d = decision.date()
    for i, s in enumerate(sessions):
        if s > d:
            return i
    return None


def price_drift(stored: float | None, sessions: list[date],
                closes: list[float], decision: datetime) -> float | None:
    """Percent gap between the price recorded at decision time and the same
    session's close as reported today. Should be ~0.

    A large gap means the series was re-adjusted underneath us, which is the
    exact failure mode this module is built to avoid. Reported rather than
    corrected: it is a data-integrity signal, and quietly patching it would
    hide the one thing worth knowing.
    """
    if not stored:
        return None
    d = decision.date()
    prior = [c for s, c in zip(sessions, closes) if s <= d]
    if not prior:
        return None
    ref = prior[-1]
    if not ref:
        return None
    return round(((ref - stored) / stored) * 100, 2)


def _parse_ts(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def evaluate_runs(runs: list[dict], closes_by_ticker: dict,
                  horizon: int = HORIZON_SESSIONS) -> tuple[list[dict], list[dict]]:
    """Pair each recorded run with its realized forward return.

    Pure: prices are handed in, so this is testable without a network. Returns
    (evaluated, pending) - pending being runs whose horizon has not elapsed
    yet, which is normal and is reported rather than dropped. Silently
    discarding them would hide how much of the record is still unresolved.
    """
    evaluated: list[dict] = []
    pending: list[dict] = []

    for r in runs:
        ticker = r.get("ticker", "")
        decision = _parse_ts(r.get("created_at", ""))
        base = {
            "ticker": ticker,
            "created_at": r.get("created_at"),
            "signal": r.get("signal"),
            "confidence": r.get("confidence"),
            "scorer": r.get("scorer"),
        }

        if decision is None:
            pending.append({**base, "reason": "unparseable timestamp"})
            continue

        series = closes_by_ticker.get(ticker)
        if not series or not series[0]:
            pending.append({**base, "reason": "no price history"})
            continue
        sessions, closes = series

        i = entry_index(sessions, decision)
        if i is None:
            pending.append({**base, "reason": "no session since the signal yet"})
            continue

        j = i + horizon
        if j >= len(sessions):
            elapsed = len(sessions) - 1 - i
            pending.append({
                **base,
                "reason": f"horizon not elapsed ({max(elapsed, 0)}/{horizon} sessions)",
            })
            continue

        entry, exit_price = closes[i], closes[j]
        ret = forward_return(entry, exit_price)
        evaluated.append({
            **base,
            "entry_date": sessions[i].isoformat(),
            "entry_price": round(entry, 2),
            "exit_date": sessions[j].isoformat(),
            "exit_price": round(exit_price, 2),
            "forward_return_pct": ret,
            "correct": direction_correct(r.get("signal", ""), ret),
            "price_drift_pct": price_drift(r.get("price_at_signal"),
                                           sessions, closes, decision),
        })

    return evaluated, pending


def _rate(hits: int, n: int) -> float | None:
    return round(100 * hits / n, 1) if n else None


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def summarize(evaluated: list[dict], pending: list[dict],
              horizon: int = HORIZON_SESSIONS) -> dict:
    """Aggregate the per-run results, with the caveats attached.

    The caveats are part of the return value on purpose. Anyone reading a hit
    rate out of this should have to read, in the same object, why it may not
    mean what they want it to mean.
    """
    directional = [e for e in evaluated if e["correct"] is not None]
    hits = sum(1 for e in directional if e["correct"])

    by_signal: dict[str, dict] = {}
    for sig in ("BUY", "HOLD", "SELL"):
        group = [e for e in evaluated if e["signal"] == sig]
        if not group:
            continue
        scored = [e for e in group if e["correct"] is not None]
        by_signal[sig] = {
            "n": len(group),
            "hit_rate_pct": _rate(sum(1 for e in scored if e["correct"]), len(scored)),
            "mean_forward_return_pct": _mean([e["forward_return_pct"] for e in group]),
        }

    by_confidence: dict[str, dict] = {}
    for bucket in ("<60", "60-74", "75+"):
        group = [e for e in directional
                 if confidence_bucket(e.get("confidence") or 0) == bucket]
        if not group:
            continue
        by_confidence[bucket] = {
            "n": len(group),
            "hit_rate_pct": _rate(sum(1 for e in group if e["correct"]), len(group)),
            "mean_forward_return_pct": _mean([e["forward_return_pct"] for e in group]),
        }

    conclusive = len(directional) >= MIN_SAMPLES

    caveats = [
        "Directional accuracy only. No transaction costs, no slippage, no "
        "market benchmark - a 55% hit rate on 0.1% moves is not a profit.",
        "HOLD runs are excluded from the hit rate; HOLD makes no directional "
        "claim, and counting them as wins would inflate the number.",
        f"Entry is the first close after the decision date, exit {horizon} "
        "sessions later. Entry and exit come from one request per ticker, so "
        "both sit on the same adjustment basis.",
    ]
    if not conclusive:
        caveats.insert(0, (
            f"NOT CONCLUSIVE: {len(directional)} directional runs, below the "
            f"{MIN_SAMPLES} needed for the number to mean anything. Treat "
            "every figure below as a plumbing check, not a result."
        ))
    if pending:
        caveats.append(f"{len(pending)} recorded runs have not resolved yet.")

    drifted = [e for e in evaluated
               if e["price_drift_pct"] is not None
               and abs(e["price_drift_pct"]) > PRICE_DRIFT_PCT]
    if drifted:
        caveats.append(
            f"{len(drifted)} run(s) show >{PRICE_DRIFT_PCT}% drift between the "
            "recorded price and today's close for that session - likely a "
            "split re-adjusting the series. Returns are computed on a single "
            "consistent basis and are unaffected, but the record is suspect."
        )

    return {
        "horizon_sessions": horizon,
        "evaluated": len(evaluated),
        "pending": len(pending),
        "directional_runs": len(directional),
        "hit_rate_pct": _rate(hits, len(directional)),
        "mean_forward_return_pct": _mean(
            [e["forward_return_pct"] for e in directional]),
        "by_signal": by_signal,
        "by_confidence": by_confidence,
        "conclusive": conclusive,
        "min_samples": MIN_SAMPLES,
        "caveats": caveats,
    }


# --- Impure edge: the one place that touches the network -------------------

def _fetch_closes(ticker: str, start: date) -> tuple[list[date], list[float]]:
    """Dated daily closes for one ticker, oldest first.

    One request per ticker covering the whole evaluation window. That is what
    guarantees entry and exit share an adjustment basis - fetching them
    separately is how a split turns into a fictional loss.
    """
    try:
        import yfinance as yf
        # A few days of head room so the session before the earliest decision
        # is present for the price-drift cross-check.
        hist = yf.Ticker(ticker).history(
            start=(start - timedelta(days=7)).isoformat(), auto_adjust=True)
        if hist.empty:
            return [], []
        sessions = [d.date() for d in hist.index.to_pydatetime()]
        closes = [float(c) for c in hist["Close"].tolist()]
        return sessions, closes
    except Exception as e:
        log.warning("could not fetch history for %s: %s", ticker, e)
        return [], []


def run_evaluation(ticker: str | None = None, horizon: int = HORIZON_SESSIONS,
                   llm_only: bool = True, limit: int = 1000,
                   path=None) -> dict:
    """Evaluate recorded runs against what prices actually did.

    llm_only defaults to True: a run that fell back to VADER measures the
    fallback, not the model, and store.py labels the scorer precisely so those
    can be excluded here instead of silently averaged in.
    """
    runs = store.history(ticker=ticker, limit=limit, path=path)
    excluded = 0
    if llm_only:
        keep = [r for r in runs if r.get("scorer") == "llm"]
        excluded = len(runs) - len(keep)
        runs = keep

    if not runs:
        out = summarize([], [], horizon)
        out["excluded_non_llm_runs"] = excluded
        out["caveats"].insert(0, "No runs recorded yet - run /analyze first.")
        return out

    dates = [_parse_ts(r.get("created_at", "")) for r in runs]
    earliest = min((d.date() for d in dates if d), default=date.today())

    closes_by_ticker = {}
    for t in {r["ticker"] for r in runs}:
        closes_by_ticker[t] = _fetch_closes(t, earliest)

    evaluated, pending = evaluate_runs(runs, closes_by_ticker, horizon)
    out = summarize(evaluated, pending, horizon)
    out["excluded_non_llm_runs"] = excluded
    out["runs"] = evaluated
    out["unresolved"] = pending
    return out
