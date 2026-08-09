"""
Keys must never reach the logs.

httpx logs every request at INFO with the full URL. NewsAPI and Alpha Vantage
both pass their key as a query parameter, so leaving httpx at INFO prints a
live credential into every terminal, screenshot and aggregated log stream.

This is a regression test for a real leak: the running server had the NewsAPI
key in its log six times before it was caught. Importing app.main must leave
httpx quiet enough that request URLs never render.
"""
import logging

import app.main  # noqa: F401  - imported for its logging configuration


def test_httpx_is_not_logging_request_urls():
    assert logging.getLogger("httpx").level >= logging.WARNING


def test_httpcore_is_not_logging_request_urls():
    assert logging.getLogger("httpcore").level >= logging.WARNING


def test_our_own_loggers_are_not_silenced():
    """Silencing httpx must not silence us - the collector's counts and the
    pipeline's timings are how a run is debugged.

    Asserts the level is unset rather than checking the effective level:
    logging.basicConfig() is a no-op when handlers already exist, which is the
    case under pytest, so the effective level here reflects pytest's root
    config and not ours. What this file is actually guarding is that we never
    reach in and raise a threshold on our own loggers the way we do on httpx.
    """
    for name in ("app.pipeline", "app.agents.collector", "app.agents.analyzer"):
        assert logging.getLogger(name).level == logging.NOTSET
