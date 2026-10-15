"""
Record real pipeline runs for the static GitHub Pages demo.

Pages can only serve files, so the demo build of the dashboard reads saved
Reports from frontend/public/demo/ instead of calling the API. This script
produces those files by running the actual pipeline - nothing in them is
hand-written.

Run from the repo root, with a working LLM key in .env:

    python scripts/snapshot_demo.py              # default tickers
    python scripts/snapshot_demo.py AAPL GME     # or pick your own

All or nothing: if any ticker's scoring or summary fell back (VADER, template
prose), no file is written. A degraded run on the public demo would quietly
misrepresent what the system does when it is working. Source warnings such as
"reddit unavailable" are allowed through; they are the pipeline being honest
about its inputs, and the dashboard shows them.

Runs are sequential with a pause between tickers. Reddit blocked this IP once
after burst traffic during development (see CLAUDE.md); do not parallelize.
"""
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.pipeline import run_pipeline  # noqa: E402

OUT_DIR = ROOT / "frontend" / "public" / "demo"
DEFAULT_TICKERS = ["AAPL", "NVDA", "TSLA", "MSFT", "AMD"]
PAUSE_SECONDS = 5


def problems(report) -> list[str]:
    """Reasons this report must not go on the public demo. Empty means fine."""
    out = []
    vader = sum(1 for s in report.scored_items if s.scorer != "llm")
    if vader:
        out.append(f"{vader} of {len(report.scored_items)} items scored by VADER")
    if not report.scored_items:
        out.append("no items scored")
    out += [w for w in report.warnings if "LLM" in w]
    return out


async def main(tickers: list[str]) -> int:
    reports = {}
    for n, t in enumerate(tickers):
        if n:
            time.sleep(PAUSE_SECONDS)
        print(f"running {t} ...", flush=True)
        report = await run_pipeline(t)
        bad = problems(report)
        if bad:
            print(f"  {t} REJECTED: " + "; ".join(bad))
            print("Nothing written. Fix the cause (usually the LLM key) and rerun.")
            return 1
        print(f"  {t} {report.signal} {report.confidence}% "
              f"score {report.overall_score:+.3f}, {report.item_count} items")
        reports[t] = report

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Replace the whole set, so the demo never mixes runs from different days
    # under one "recorded on" date.
    for old in OUT_DIR.glob("*.json"):
        old.unlink()
    for t, report in reports.items():
        (OUT_DIR / f"{t}.json").write_text(
            json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8")
    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tickers": list(reports),
    }
    (OUT_DIR / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"wrote {len(reports)} snapshots to {OUT_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main([t.upper() for t in sys.argv[1:]] or DEFAULT_TICKERS)))
