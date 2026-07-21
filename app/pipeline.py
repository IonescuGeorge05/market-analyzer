"""
ORCHESTRATION

The whole multi-agent system is these three lines. Each agent only knows
the schema of the one before it - swap any agent's internals and the
others do not care. That decoupling is the point.
"""
import logging
import time

from app.agents import analyzer, collector, reporter
from app.schemas import Report

log = logging.getLogger(__name__)


async def run_pipeline(ticker: str) -> Report:
    t0 = time.perf_counter()

    collected = await collector.run(ticker)
    log.info("agent1 collected %d items", len(collected.items))

    sentiment = await analyzer.run(collected)
    log.info("agent2 scored %s -> %s", sentiment.overall_score,
             sentiment.classification)

    report = await reporter.run(sentiment)
    log.info("agent3 signal %s (%d%%) in %.1fs", report.signal,
             report.confidence, time.perf_counter() - t0)

    return report
