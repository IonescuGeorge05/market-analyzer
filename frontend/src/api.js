// Talks to the FastAPI backend. One call per analysis: /analyze returns the
// signal, the prose and the scored evidence together, so the dashboard never
// pays for LLM scoring twice.
//
// Demo mode (VITE_DEMO=true, set by `npm run build:demo`) is for the GitHub
// Pages build. Pages only serves static files, so there is no backend to
// call: analyze() reads a saved Report from public/demo/<TICKER>.json
// instead. Those files are real pipeline output, recorded by
// scripts/snapshot_demo.py. The rest of the app cannot tell the difference.

const BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

export const DEMO = import.meta.env.VITE_DEMO === "true";

// Vite's base path: "/" locally, "/market-analyzer/" on Pages.
const DEMO_DIR = `${import.meta.env.BASE_URL}demo`;

export async function demoIndex() {
  const res = await fetch(`${DEMO_DIR}/index.json`);
  if (!res.ok) throw new Error("demo snapshots missing");
  return res.json();
}

export async function analyze(ticker) {
  if (DEMO) {
    const res = await fetch(`${DEMO_DIR}/${encodeURIComponent(ticker)}.json`);
    if (!res.ok) {
      throw new Error(
        `${ticker} is not in the demo. Pick one of the saved tickers above, ` +
        "or run the project locally to analyze anything."
      );
    }
    return res.json();
  }

  const res = await fetch(`${BASE}/analyze/${encodeURIComponent(ticker)}`);

  if (!res.ok) {
    // FastAPI puts the reason in `detail`; fall back to the status text.
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body.detail) detail = body.detail;
    } catch {
      /* non-JSON error body, keep the status text */
    }
    throw new Error(`${res.status}: ${detail}`);
  }

  return res.json();
}

export async function health() {
  const res = await fetch(`${BASE}/health`);
  if (!res.ok) throw new Error("backend unreachable");
  return res.json();
}
