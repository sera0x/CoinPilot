import { useEffect, useState } from "react";
import { motion, useSpring } from "motion/react";
import Swarm from "./Swarm";
import type { DashboardData } from "./types";

const GROUPS = ["depth", "turnover", "pressure", "momentum1h", "momentum6h", "mania", "age", "extras"];

const REPO = "sera0x/CoinPilot";
// The pulse workflow commits a fresh data.json to the ledger branch every 2
// minutes; the deployed Pages copy only refreshes on a full cycle. When the
// local feed goes stale, the dashboard reads the ledger's copy directly.
const LEDGER_DATA = `https://raw.githubusercontent.com/${REPO}/ledger/site/data.json`;

const money = (v: number | null | undefined) =>
  v == null ? "—" : "$" + Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
const compact = (v: number | null | undefined) => {
  if (v == null) return "—";
  const n = Number(v);
  if (n >= 1_000_000) return "$" + (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return "$" + (n / 1_000).toFixed(1) + "K";
  return "$" + n.toFixed(0);
};
const pct = (v: number | null | undefined) => (v == null ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(1) + "%");
const cls = (v: number | null | undefined) => ((v ?? 0) >= 0 ? "up" : "down");
const esc = (s: unknown) => String(s ?? "");
const when = (ts: number) => new Date(ts * 1000).toLocaleString(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });

function CountUp({ value, decimals = 2, prefix = "", suffix = "", className }: {
  value: number; decimals?: number; prefix?: string; suffix?: string; className?: string;
}) {
  const spring = useSpring(value, { stiffness: 60, damping: 18 });
  const [display, setDisplay] = useState(value);
  useEffect(() => {
    spring.set(value);
  }, [value, spring]);
  useEffect(() => {
    return spring.on("change", (v) => setDisplay(v));
  }, [spring]);
  return (
    <span className={className}>
      {prefix}
      {Number(display).toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}
      {suffix}
    </span>
  );
}

function Card({ title, count, children, delay = 0 }: {
  title: string; count?: string | number; children: React.ReactNode; delay?: number;
}) {
  return (
    <motion.section
      className="card"
      initial={{ opacity: 0, y: 14 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-40px" }}
      transition={{ duration: 0.5, delay }}
    >
      <h2>
        {title}
        {count != null && <span className="count">{String(count)}</span>}
      </h2>
      {children}
    </motion.section>
  );
}

function Curve({ series, start }: { series: number[]; start: number }) {
  if (series.length < 2) return <p className="muted">No closed trades yet — the curve starts with the first round trip.</p>;
  const w = 620, h = 110;
  const low = Math.min(...series), high = Math.max(...series);
  const span = high - low || 1;
  const pts = series.map((v, i) => `${(i / (series.length - 1)) * w},${h - 10 - ((v - low) / span) * (h - 20)}`);
  const stroke = series[series.length - 1] >= start ? "#3fb950" : "#f85149";
  return (
    <svg className="curve" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <polyline points={pts.join(" ")} fill="none" stroke={stroke} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

function LearningPanel({ learning }: { learning: DashboardData["learning"] }) {
  if (!learning || !learning.mult) return <p className="muted">No learning data yet.</p>;
  return (
    <div>
      <p className="muted">
        After every closed trade the bot re-weights the signals that called it right and down-weights the ones that
        called it wrong, clamped to ×0.5–×2.0 of base. {learning.n} trade(s) graded so far — with single-position
        sizing at ${"50"} a trade this needs weeks of round trips before the deltas mean much.
      </p>
      {GROUPS.map((g) => {
        const mult = learning.mult[g] ?? 1;
        const st = learning.stats?.[g] ?? { pos_n: 0, pos_sum: 0, neg_n: 0, neg_sum: 0 };
        const width = Math.min(100, Math.abs((mult - 1) * 100));
        const avgPos = st.pos_n ? (st.pos_sum / st.pos_n).toFixed(1) : "—";
        const avgNeg = st.neg_n ? (st.neg_sum / st.neg_n).toFixed(1) : "—";
        return (
          <div className="learn-row" key={g}>
            <div className="learn-head">
              <b>{g}</b>
              <span className={mult >= 1 ? "up" : "down"}>×{mult.toFixed(2)}</span>
            </div>
            <div className="bar">
              <i className={mult >= 1 ? "pos" : "neg"} style={{ width: `${width}%` }} />
            </div>
            <div className="muted learn-stats">
              won {st.pos_n} (avg {avgPos}%) · lost {st.neg_n} (avg {avgNeg}%)
            </div>
          </div>
        );
      })}
    </div>
  );
}

function AuditPanel({ audit }: { audit: DashboardData["audit"] }) {
  if (!audit) return <p className="muted">No audit data yet.</p>;
  const { adjustments, edges, band } = audit;
  return (
    <div>
      <p className="muted">
        After every cycle the auditor grades its own results against the archive: what actually happened to tokens it
        priced at or above the entry bar, versus below it. When the evidence is strong, it moves buy_score — clamped to
        ±10 points of the configured base, one 2-point step at a time, at most once per 3 hours.
      </p>
      {band && (band.above.n > 0 || band.below.n > 0) && (
        <p className="muted">
          <b>Hindsight so far:</b> {band.above.n} token(s) at/above the bar averaged{" "}
          <span className={cls(band.above.avg_outcome ?? 0)}>{pct(band.above.avg_outcome)}</span> after being seen ·{" "}
          {band.below.n} below the bar averaged <span className={cls(band.below.avg_outcome ?? 0)}>{pct(band.below.avg_outcome)}</span>.
        </p>
      )}
      {edges.length > 0 && (
        <table>
          <thead>
            <tr><th>Score band at sighting</th><th>Studied</th><th>Avg outcome since</th></tr>
          </thead>
          <tbody>
            {edges.map((e) => (
              <tr key={e.bucket}>
                <td>{esc(e.bucket)}</td>
                <td className="num">{e.n}</td>
                <td className={`num ${cls(e.avg_outcome ?? 0)}`}>{pct(e.avg_outcome)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {adjustments.length > 0 ? (
        <table>
          <thead>
            <tr><th>When</th><th>Change</th><th>Evidence</th></tr>
          </thead>
          <tbody>
            {adjustments.map((a, i) => (
              <tr key={i}>
                <td className="muted">{when(a.ts)}</td>
                <td>
                  <b>{esc(a.kind)}</b> {a.value_from == null ? "" : `${a.value_from} → ${a.value_to}`}
                </td>
                <td className="muted">{esc(a.reason)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="muted">No parameter changes yet — the archive is still filling. The auditor starts nudging buy_score once ~12 studied sightings sit in the entry band.</p>
      )}
    </div>
  );
}

export default function App() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const loadFrom = async (url: string): Promise<DashboardData> => {
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return (await r.json()) as DashboardData;
    };
    const apply = (d: DashboardData) => {
      if (alive) {
        setData(d);
        setError(null);
      }
    };
    const load = async () => {
      try {
        const local = await loadFrom("./data.json");
        apply(local);
        // Stale deployed feed (Pages redeploy lags the 2-minute pulse): fall
        // through to the ledger branch's copy, which the pulse writes directly.
        const age = (Date.now() - new Date(local.generated).getTime()) / 1000;
        if (age > 180) {
          try {
            apply(await loadFrom(LEDGER_DATA));
          } catch {
            /* keep the local copy */
          }
        }
      } catch {
        try {
          apply(await loadFrom(LEDGER_DATA));
        } catch (e) {
          if (alive) setError(String(e));
        }
      }
    };
    load();
    const timer = setInterval(load, 60_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const healthy = data ? data.account.equity >= data.account.starting_cash : true;
  const cutoff = Date.now() / 1000 - 86_400;
  const recentTrades = data ? data.trades.filter((t) => t.side === "sell" && t.ts >= cutoff).length : 0;
  const activity = Math.min(1.5, 0.25 + recentTrades * 0.25 + (data?.positions.length ?? 0) * 0.15);

  return (
    <>
      <header className="hero">
        <Swarm activity={activity} healthy={healthy} />
        <div className="hero-inner">
          <p className="eyebrow">Paper trading desk · agent swarm live</p>
          <h1>CoinPilot</h1>
          {data ? (
            <>
              <div className="kpis">
                <div className="stat">
                  <b><CountUp value={data.account.equity} prefix="$" /></b>
                  <span>Equity</span>
                </div>
                <div className="stat">
                  <b><CountUp value={data.account.unrealized} prefix="$" className={cls(data.account.unrealized)} /></b>
                  <span>Unrealized</span>
                </div>
                <div className="stat">
                  <b><CountUp value={data.account.realized_today} prefix="$" className={cls(data.account.realized_today)} /></b>
                  <span>Realized today</span>
                </div>
                <div className="stat">
                  <b><CountUp value={data.account.realized_total} prefix="$" className={cls(data.account.realized_total)} /></b>
                  <span>Realized all-time</span>
                </div>
                <div className="stat">
                  <b><CountUp value={data.win_stats.win_rate} decimals={0} suffix="%" /></b>
                  <span>Win rate</span>
                </div>
                <div className="stat">
                  <b>{data.account.kill ? "KILLED" : "live"}</b>
                  <span>Kill switch</span>
                </div>
              </div>
              <p className="muted">
                {data.chain} · {data.positions.length} open · {data.win_stats.closed} closed · updated{" "}
                {data.generated.replace("T", " ")} UTC · simulated fills only
              </p>
            </>
          ) : (
            <p className="muted">{error ? `Waiting for the bot's first data.json publish (${error})` : "Connecting to the ledger…"}</p>
          )}
        </div>
      </header>

      <main>
        <div className="grid two">
          <Card title="Open positions" count={data?.positions.length ?? 0}>
            {data && data.positions.length > 0 ? (
              <table>
                <thead>
                  <tr><th>Token</th><th>Entry $</th><th>Mark</th><th>PnL</th><th>Held</th></tr>
                </thead>
                <tbody>
                  {data.positions.map((p) => (
                    <tr key={p.id}>
                      <td><a href={`https://dexscreener.com/${data.chain}/${p.token_address}`} target="_blank" rel="noopener">{esc(p.symbol)}</a></td>
                      <td className="num">{money(p.entry_usd)}</td>
                      <td className="num">{p.mark_price}</td>
                      <td className={`num ${cls(p.pnl_usd)}`}>{money(p.pnl_usd)} ({pct(p.pnl_pct)})</td>
                      <td className="num">{Math.round(p.held_minutes)}m</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="muted">None open.</p>
            )}
          </Card>

          <Card title="Equity curve" count={data?.win_stats.closed ?? 0}>
            {data ? <Curve series={data.curve} start={data.account.starting_cash} /> : <p className="muted">—</p>}
          </Card>
        </div>

        <Card title="Watchlist — what the swarm found" count={data?.watchlist.length ?? 0} delay={0.05}>
          {data && data.watchlist.length > 0 ? (
            <div className="scroll-y">
              <table>
                <thead>
                  <tr><th>Score</th><th>Token</th><th>Liquidity</th><th>Vol 24h</th><th>Age</th><th>24h</th><th>Seen</th><th>Status</th><th>Credibility</th><th>Whale flow</th></tr>
                </thead>
                <tbody>
                  {data.watchlist.map((w, i) => (
                    <tr key={`${w.token_address}-${i}`}>
                      <td className="num"><b>{w.score}</b></td>
                      <td><a href={`https://dexscreener.com/${data.chain}/${w.token_address}`} target="_blank" rel="noopener">{esc(w.symbol)}</a></td>
                      <td className="num">{compact(w.liquidity)}</td>
                      <td className="num">{compact(w.volume)}</td>
                      <td className="num">{w.age == null ? "—" : Math.round(w.age) + "m"}</td>
                      <td className={`num ${cls(w.change)}`}>{pct(w.change)}</td>
                      <td className="num">{w.corroboration}x</td>
                      <td><span className={`pill ${esc(w.status)}`}>{esc(w.status)}</span></td>
                      <td className="muted cred">{esc(w.credibility)}</td>
                      <td className="muted cred">{esc(w.flows)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">Nothing scored yet.</p>
          )}
        </Card>

        <div className="grid two">
          <Card title="Recent trades" count={data?.trades.length ?? 0} delay={0.1}>
            {data && data.trades.length > 0 ? (
              <div className="scroll-y">
              <table>
                <thead>
                  <tr><th>When</th><th>Side</th><th>Token</th><th>USD</th><th>PnL</th><th>Reason</th></tr>
                </thead>
                <tbody>
                  {data.trades.map((t, i) => (
                    <tr key={i}>
                      <td className="muted">{when(t.ts)}</td>
                      <td className={t.side === "buy" ? "up" : "down"}>{t.side.toUpperCase()}</td>
                      <td>{esc(t.symbol)}</td>
                      <td className="num">{money(t.usd)}</td>
                      <td className={`num ${t.pnl == null ? "muted" : cls(t.pnl)}`}>{t.pnl == null ? "—" : money(t.pnl)}</td>
                      <td className="muted">{esc(t.reason)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            ) : (
              <p className="muted">No trades yet.</p>
            )}
          </Card>

          <Card title="Analyst verdicts" count={data?.verdicts.length ?? 0} delay={0.15}>
            {data && data.verdicts.length > 0 ? (
              <div className="scroll-y">
              <table>
                <thead>
                  <tr><th>When</th><th>Token</th><th>Verdict</th><th>Thesis</th></tr>
                </thead>
                <tbody>
                  {data.verdicts.map((v, i) => (
                    <tr key={i}>
                      <td className="muted">{when(v.ts)}</td>
                      <td>{esc(v.symbol)}</td>
                      <td className={v.action === "buy" ? "up" : "muted"}>{esc(v.action)} {v.score ?? ""}</td>
                      <td className="muted">{esc(v.thesis)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            ) : (
              <p className="muted">No verdicts yet.</p>
            )}
          </Card>
        </div>

        <Card title="Self-learning — what the bot has figured out" delay={0.2}>
          {data ? <LearningPanel learning={data.learning} /> : <p className="muted">—</p>}
        </Card>

        <Card title="Self-audit — the flaws it found and what it changed" delay={0.25}>
          {data ? <AuditPanel audit={data.audit} /> : <p className="muted">—</p>}
        </Card>

        <p className="foot">
          Simulated fills only · no wallet · no keys · nothing is signed. A public paper ledger, not financial advice.{" "}
          <a href="https://github.com/sera0x/CoinPilot/blob/main/vps/README.md" target="_blank" rel="noopener">
            Run it on your own VPS
          </a>{" "}
          ·{" "}
          <a href="https://raw.githubusercontent.com/sera0x/CoinPilot/main/vps/agent-prompt.md" target="_blank" rel="noopener">
            agent setup prompt
          </a>
        </p>
      </main>
    </>
  );
}
