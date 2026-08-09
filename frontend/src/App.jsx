import { useEffect, useState } from "react";
import {
  Bar, BarChart, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { analyze, health } from "./api";
import "./App.css";

const SIGNAL_CLASS = { BUY: "buy", HOLD: "hold", SELL: "sell" };

// Same thresholds as reporter._signal(). Kept in sync deliberately: the
// frontend only colours things, it never decides anything.
function scoreColor(v) {
  if (v > 0.15) return "var(--buy)";
  if (v < -0.15) return "var(--sell)";
  return "var(--hold)";
}

function signed(v, digits = 2) {
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}`;
}

function SignalCard({ report }) {
  return (
    <section className={`signal-card ${SIGNAL_CLASS[report.signal]}`}>
      <div className="signal-main">
        <span className="signal-label">Signal</span>
        <span className="signal-value">{report.signal}</span>
      </div>

      <div className="signal-meta">
        <div className="meter">
          <div className="meter-top">
            <span>Confidence</span>
            <span className="num">{report.confidence}%</span>
          </div>
          <div className="meter-track">
            <div className="meter-fill" style={{ width: `${report.confidence}%` }} />
          </div>
        </div>

        <dl className="facts">
          <div>
            <dt>Sentiment</dt>
            <dd>
              {report.classification}{" "}
              <span className="num" style={{ color: scoreColor(report.overall_score) }}>
                {signed(report.overall_score, 3)}
              </span>
            </dd>
          </div>
          <div>
            <dt>Sources</dt>
            <dd className="num">{report.item_count}</dd>
          </div>
        </dl>
      </div>
    </section>
  );
}

function PriceCard({ price }) {
  if (price?.current == null) {
    return (
      <section className="card">
        <h2>Price</h2>
        <p className="empty">
          No price data — yfinance returned nothing for this ticker, and the
          Alpha Vantage fallback did not fill the gap either.
        </p>
      </section>
    );
  }
  const up = (price.change_pct ?? 0) >= 0;
  return (
    <section className="card">
      <h2>Price</h2>
      <p className="price-now num">${price.current.toFixed(2)}</p>
      <p className={`price-change num ${up ? "up" : "down"}`}>
        {up ? "▲" : "▼"} {Math.abs(price.change_pct).toFixed(2)}%
        <span className="dim"> over 5 sessions</span>
      </p>
      <p className="dim">
        Trend <strong className="trend">{price.trend}</strong>
        {price.week_ago != null && <span className="num"> · was ${price.week_ago.toFixed(2)}</span>}
      </p>
    </section>
  );
}

function ChartTip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="tip">
      <p className="tip-title">{d.title}</p>
      <p className="tip-meta">
        <span className={`pill ${d.kind}`}>{d.kind}</span>
        {d.source} · weight {d.weight}
      </p>
      <p className="num tip-score" style={{ color: scoreColor(d.score) }}>
        {signed(d.score)}
      </p>
      {d.reasoning && <p className="tip-reason">{d.reasoning}</p>}
    </div>
  );
}

function ScoreChart({ items }) {
  if (!items.length) return null;
  const data = [...items]
    .sort((a, b) => a.score - b.score)
    .map((it, i) => ({ ...it, i }));

  return (
    <section className="card">
      <h2>Item scores</h2>
      <p className="card-note">
        Every collected item, scored −1 bearish to +1 bullish. Hover for the model's reasoning.
      </p>
      <div className="chart-wrap">
        <ResponsiveContainer width="100%" height={Math.max(200, data.length * 24)}>
          <BarChart data={data} layout="vertical" margin={{ left: 0, right: 12, top: 4, bottom: 4 }}>
            <XAxis
              type="number"
              domain={[-1, 1]}
              ticks={[-1, -0.5, 0, 0.5, 1]}
              stroke="var(--muted-t)"
              fontSize={11}
              tickLine={false}
            />
            <YAxis type="category" dataKey="i" hide />
            <ReferenceLine x={0} stroke="var(--rule)" />
            <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTip />} />
            <Bar dataKey="score" radius={2} isAnimationActive={false}>
              {data.map((d) => (
                <Cell key={d.i} fill={scoreColor(d.score)} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

function Evidence({ items }) {
  if (!items.length) return null;
  const sorted = [...items].sort((a, b) => b.score - a.score);
  return (
    <section className="card">
      <h2>Sources</h2>
      <ul className="evidence">
        {sorted.map((it, i) => (
          <li key={i}>
            <span className="ev-score num" style={{ color: scoreColor(it.score) }}>
              {signed(it.score)}
            </span>
            <span className="ev-body">
              <span className="ev-title">{it.title}</span>
              <span className="ev-meta">
                <span className={`pill ${it.kind}`}>{it.kind}</span>
                {it.source}
                {it.reasoning && <span className="dim"> — {it.reasoning}</span>}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Bullets({ title, items, tone }) {
  if (!items?.length) return null;
  return (
    <section className="card">
      <h2>{title}</h2>
      <ul className={`bullets ${tone}`}>
        {items.map((t, i) => (
          <li key={i}>{t}</li>
        ))}
      </ul>
    </section>
  );
}

export default function App() {
  const [ticker, setTicker] = useState("AAPL");
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [online, setOnline] = useState(null);

  useEffect(() => {
    health().then(() => setOnline(true)).catch(() => setOnline(false));
  }, []);

  async function run(e) {
    e?.preventDefault();
    const t = ticker.trim().toUpperCase();
    if (!t) return;
    setLoading(true);
    setError(null);
    try {
      const data = await analyze(t);
      setReport(data);
      setOnline(true);
    } catch (err) {
      setError(err.message);
      setReport(null);
    } finally {
      setLoading(false);
    }
  }

  const scored = report?.scored_items ?? [];
  // The pipeline degrades instead of failing, so a warning is normal, not fatal.
  const degraded = report?.warnings?.length > 0;

  return (
    <div className="wrap">
      <header className="mast">
        <div>
          <p className="eyebrow">Multi-agent pipeline</p>
          <h1>Market Sentiment Analyzer</h1>
        </div>
        <span className={`status ${online === false ? "off" : online ? "on" : ""}`}>
          {online === false ? "backend offline" : online ? "backend online" : "checking…"}
        </span>
      </header>

      <form className="search" onSubmit={run}>
        <input
          value={ticker}
          onChange={(e) => setTicker(e.target.value)}
          placeholder="Ticker, e.g. AAPL"
          aria-label="Ticker symbol"
          maxLength={10}
          spellCheck="false"
          autoComplete="off"
        />
        <button type="submit" disabled={loading || !ticker.trim()}>
          {loading ? "Analyzing…" : "Analyze"}
        </button>
      </form>

      {loading && (
        <p className="hint">
          Collecting news, Reddit and price, scoring each item, writing the report.
          This takes a few seconds.
        </p>
      )}

      {error && (
        <section className="card error">
          <h2>Request failed</h2>
          <p>{error}</p>
          {online === false && (
            <p className="dim">
              Start the API with <code>uvicorn app.main:app --reload</code>
            </p>
          )}
        </section>
      )}

      {report && !loading && (
        <>
          <SignalCard report={report} />

          {degraded && (
            <section className="card warn">
              <h2>Ran degraded</h2>
              <p className="card-note">
                The pipeline never fails hard. These sources were missing or failed,
                and confidence was reduced to match.
              </p>
              <ul>
                {report.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </section>
          )}

          <div className="grid">
            <section className="card">
              <h2>Summary</h2>
              <p>{report.summary}</p>
            </section>
            <PriceCard price={report.price} />
          </div>

          <div className="grid">
            <Bullets title="Key drivers" items={report.key_drivers} tone="pos" />
            <Bullets title="Risk factors" items={report.risk_factors} tone="neg" />
          </div>

          <ScoreChart items={scored} />
          <Evidence items={scored} />
        </>
      )}

      <footer>Sentiment analysis of public sources. Not investment advice.</footer>
    </div>
  );
}
