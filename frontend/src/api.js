// Talks to the FastAPI backend. One call per analysis: /analyze returns the
// signal, the prose and the scored evidence together, so the dashboard never
// pays for LLM scoring twice.

const BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

export async function analyze(ticker) {
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
