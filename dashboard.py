#!/usr/bin/env python3
"""Build the static dashboard (one self-contained HTML file) from the ledger.

Used by the GitHub Actions workflow: the bot runs a cycle, this renders the
state into dist/index.html, and Pages serves it.

The page is fully rendered here; JavaScript is a progressive layer that adds a
WebGL orb, a grain overlay, count-up numbers and staggered reveals. Everything
still reads correctly with scripts blocked or the CDNs unreachable.
"""

from __future__ import annotations

import argparse
import html
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from coinpilot import config as config_mod
from coinpilot import learn, market, store

CSS = """
:root {
  --bg: #06080b;
  --panel: rgba(17, 21, 27, 0.74);
  --line: rgba(255, 255, 255, 0.075);
  --ink: #e6edf3;
  --muted: #8b949e;
  --up: #3fb950;
  --down: #f85149;
  --accent: #58a6ff;
  --mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, monospace;
}
* { box-sizing: border-box; }
html, body { margin: 0; }
body {
  min-height: 100vh;
  color: var(--ink);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  -webkit-font-smoothing: antialiased;
  background:
    radial-gradient(1100px 620px at 18% -12%, rgba(88, 166, 255, 0.12), transparent 62%),
    radial-gradient(900px 520px at 92% -4%, rgba(63, 185, 80, 0.09), transparent 58%),
    var(--bg);
}
.grain {
  position: fixed; inset: -40px; z-index: 0; pointer-events: none;
  opacity: 0.5; mix-blend-mode: soft-light;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='160' height='160' filter='url(%23n)' opacity='0.32'/%3E%3C/svg%3E");
  background-size: 160px 160px;
  animation: grain 5s steps(5) infinite;
}
@keyframes grain {
  0% { transform: translate(0, 0); }
  20% { transform: translate(-1.6%, 1.2%); }
  40% { transform: translate(1.4%, -1.5%); }
  60% { transform: translate(-1.1%, -0.9%); }
  80% { transform: translate(1.7%, 0.8%); }
  100% { transform: translate(0, 0); }
}
.wrap { position: relative; z-index: 1; max-width: 1240px; margin: 0 auto; padding: 26px 20px 64px; }
.hero {
  display: grid; grid-template-columns: 232px minmax(0, 1fr); gap: 28px; align-items: center;
  padding: 22px 26px; margin-bottom: 18px; border: 1px solid var(--line); border-radius: 22px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.045), rgba(255, 255, 255, 0.012));
  backdrop-filter: blur(14px);
}
.orb-wrap { position: relative; width: 224px; height: 224px; justify-self: center; }
#orb { display: block; width: 100%; height: 100%; border-radius: 50%; filter: drop-shadow(0 22px 48px rgba(0, 0, 0, 0.55)); }
.orb-fallback {
  display: none; width: 100%; height: 100%; border-radius: 50%;
  background: radial-gradient(circle at 34% 28%, var(--orb-hi, #7ee7a0), var(--orb-lo, #14532d) 58%, rgba(4, 8, 12, 0.9) 100%);
  box-shadow: inset 0 0 60px rgba(255, 255, 255, 0.16), 0 22px 48px rgba(0, 0, 0, 0.55);
  animation: breathe 7s ease-in-out infinite;
}
html.no-webgl #orb { display: none; }
html.no-webgl .orb-fallback { display: block; }
@keyframes breathe {
  0%, 100% { transform: scale(1); filter: brightness(1); }
  50% { transform: scale(1.035); filter: brightness(1.16); }
}
.eyebrow { margin: 0; font-size: 10.5px; letter-spacing: 0.18em; text-transform: uppercase; color: var(--muted); }
h1 { margin: 4px 0 6px; font-size: 30px; letter-spacing: -0.02em; font-weight: 600; }
.lede { margin: 0 0 14px; color: var(--muted); }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(118px, 1fr)); gap: 10px; }
.kpi {
  padding: 9px 12px; border: 1px solid var(--line); border-radius: 13px;
  background: rgba(255, 255, 255, 0.028);
}
.kpi span { display: block; font-size: 9.5px; letter-spacing: 0.09em; text-transform: uppercase; color: var(--muted); }
.kpi b { display: block; margin-top: 3px; font-family: var(--mono); font-size: 17px; font-variant-numeric: tabular-nums; }
.card {
  padding: 15px 17px; margin-bottom: 16px; overflow: auto;
  border: 1px solid var(--line); border-radius: 16px; background: var(--panel);
  backdrop-filter: blur(10px);
}
.grid.two { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
h2 {
  margin: 0 0 12px; font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--muted);
  display: flex; align-items: center; gap: 8px;
}
h2 .count {
  font-family: var(--mono); font-size: 10px; letter-spacing: 0; color: var(--ink);
  background: rgba(255, 255, 255, 0.07); border-radius: 20px; padding: 1px 8px;
}
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th {
  position: sticky; top: 0; text-align: left; font-weight: 500; color: var(--muted);
  padding: 5px 8px; border-bottom: 1px solid var(--line); background: rgba(8, 11, 15, 0.86);
  backdrop-filter: blur(4px); white-space: nowrap;
}
td { padding: 7px 8px; border-bottom: 1px solid rgba(255, 255, 255, 0.04); white-space: nowrap; }
tbody tr:hover td { background: rgba(255, 255, 255, 0.026); }
td.num, th.num { text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; }
td.wide { white-space: normal; min-width: 190px; color: var(--muted); font-size: 12px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.up { color: var(--up); }
.down { color: var(--down); }
.muted { color: var(--muted); }
.pill {
  display: inline-block; padding: 1px 8px; border-radius: 20px; font-size: 11px;
  border: 1px solid var(--line); color: var(--muted);
}
.pill.entered { color: #56d364; border-color: rgba(63, 185, 80, 0.4); background: rgba(63, 185, 80, 0.1); }
.pill.blocked { color: #ffa198; border-color: rgba(248, 81, 73, 0.4); background: rgba(248, 81, 73, 0.09); }
.pill.judged { color: #79c0ff; border-color: rgba(88, 166, 255, 0.4); background: rgba(88, 166, 255, 0.09); }
.curve { width: 100%; height: 132px; display: block; }
.foot { margin-top: 26px; color: var(--muted); font-size: 12px; text-align: center; }
.reveal { opacity: 0; transform: translateY(10px); }
.reveal.in {
  opacity: 1; transform: none;
  transition: opacity 0.55s ease, transform 0.55s cubic-bezier(0.2, 0.7, 0.2, 1);
}
@media (max-width: 880px) {
  .hero { grid-template-columns: 1fr; }
  .grid.two { grid-template-columns: 1fr; }
  h1 { font-size: 26px; }
}
@media (prefers-reduced-motion: reduce) {
  .reveal { opacity: 1; transform: none; }
  .grain { animation: none; }
  .orb-fallback { animation: none; }
}
"""

JS = """
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function formatNumber(value, decimals) {
  return value.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function countUp(el) {
  const target = parseFloat(el.dataset.count);
  if (!Number.isFinite(target)) return;
  const decimals = parseInt(el.dataset.decimals || "2", 10);
  const prefix = el.dataset.prefix || "";
  const suffix = el.dataset.suffix || "";
  if (reduceMotion) {
    el.textContent = prefix + formatNumber(target, decimals) + suffix;
    return;
  }
  const start = performance.now();
  const duration = 850;
  const tick = (now) => {
    const progress = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - progress, 3);
    el.textContent = prefix + formatNumber(target * eased, decimals) + suffix;
    if (progress < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

document.querySelectorAll("[data-count]").forEach(countUp);

const revealer = new IntersectionObserver(
  (entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        entry.target.classList.add("in");
        revealer.unobserve(entry.target);
      }
    }
  },
  { rootMargin: "0px 0px -6% 0px" }
);
document.querySelectorAll(".reveal").forEach((el) => revealer.observe(el));

// Optional: Motion (motion.dev) adds a stagger to table rows and cards.
(async () => {
  if (reduceMotion) return;
  try {
    const { animate, stagger } = await import("https://cdn.jsdelivr.net/npm/motion@11/+esm");
    animate(
      "tbody tr",
      { opacity: [0, 1], transform: ["translateY(6px)", "translateY(0px)"] },
      { delay: stagger(0.012, { start: 0.05 }), duration: 0.4, easing: "ease-out" }
    );
    animate(".kpi", { transform: ["translateY(4px)", "translateY(0px)"], opacity: [0.6, 1] }, { delay: stagger(0.03), duration: 0.45 });
  } catch (error) {
    // Motion is a progressive enhancement; the CSS reveal already covered it.
  }
})();

// Optional: three.js renders the orb. Without WebGL the CSS orb shows instead.
(() => {
  const canvas = document.getElementById("orb");
  if (!canvas) return;
  import("https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js")
    .then((THREE) => drawOrb(THREE, canvas))
    .catch(() => document.documentElement.classList.add("no-webgl"));
})();

function drawOrb(THREE, canvas) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  } catch (error) {
    document.documentElement.classList.add("no-webgl");
    return;
  }
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
  camera.position.z = 3.15;

  const uniforms = {
    uTime: { value: 0 },
    uTint: { value: new THREE.Color(canvas.dataset.tint || "#3fb950") },
  };

  const material = new THREE.ShaderMaterial({
    uniforms,
    transparent: true,
    depthWrite: false,
    vertexShader: `
      uniform float uTime;
      varying float vNoise;
      varying vec3 vNormal;
      varying vec3 vView;
      void main() {
        vec3 p = position;
        float n = sin(p.x * 2.6 + uTime * 0.9) * sin(p.y * 2.2 - uTime * 0.7) * sin(p.z * 2.9 + uTime * 0.5);
        n += 0.45 * sin(p.y * 5.3 + uTime * 1.5);
        vNoise = n;
        p += normal * n * 0.085;
        vNormal = normalize(normalMatrix * normal);
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        vView = normalize(-mv.xyz);
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: `
      uniform vec3 uTint;
      varying float vNoise;
      varying vec3 vNormal;
      varying vec3 vView;
      void main() {
        float facing = max(dot(normalize(vNormal), normalize(vView)), 0.0);
        float rim = pow(1.0 - facing, 2.3);
        float band = 0.5 + 0.5 * sin(vNoise * 3.4);
        vec3 color = mix(uTint * 0.32, uTint, clamp(rim, 0.0, 1.0));
        float alpha = clamp(0.16 + rim * 0.9, 0.0, 0.95);
        gl_FragColor = vec4(color * (0.55 + 0.65 * band), alpha);
      }
    `,
  });

  const mesh = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 48), material);
  scene.add(mesh);

  const resize = () => {
    const rect = canvas.getBoundingClientRect();
    const size = Math.max(rect.width || 224, 1);
    renderer.setSize(size, size, false);
    camera.aspect = 1;
    camera.updateProjectionMatrix();
  };
  resize();
  window.addEventListener("resize", resize);

  const clock = new THREE.Clock();
  const frame = () => {
    const t = clock.getElapsedTime();
    uniforms.uTime.value = t;
    mesh.rotation.y = t * 0.14;
    mesh.rotation.x = Math.sin(t * 0.22) * 0.14;
    renderer.render(scene, camera);
    if (!reduceMotion) requestAnimationFrame(frame);
  };
  frame();
}
"""

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="robots" content="noindex">
<title>CoinPilot - paper desk</title>
<style>
%%CSS%%
</style>
</head>
<body>
<div class="grain" aria-hidden="true"></div>
%%BODY%%
<script type="module">
%%JS%%
</script>
</body>
</html>
"""

STAGE_LABELS = {
    "seen": "seen",
    "judged": "judged",
    "entered": "entered",
    "blocked": "blocked",
    "skip": "skipped",
}


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def money(value) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def number(value, decimals: int = 1) -> str:
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


def compact(value) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "-"
    if abs(amount) >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if abs(amount) >= 1_000:
        return f"${amount / 1_000:.0f}k"
    return f"${amount:.0f}"


def price_text(value) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "-"
    if amount <= 0:
        return "0"
    if amount < 0.0001:
        return f"{amount:.10f}"
    if amount < 1:
        return f"{amount:.6f}"
    return f"{amount:,.4f}"


def pct_class(value) -> str:
    try:
        return "up" if float(value) >= 0 else "down"
    except (TypeError, ValueError):
        return "muted"


def kpi(label: str, value: float, decimals: int, prefix: str = "", suffix: str = "", cls: str = "") -> str:
    initial = prefix
    try:
        initial += f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        initial += "-"
    initial += suffix
    return (
        f'<div class="kpi reveal"><span>{esc(label)}</span>'
        f'<b class="{cls}" data-count="{number(value, 6)}" data-decimals="{decimals}" '
        f'data-prefix="{esc(prefix)}" data-suffix="{esc(suffix)}">{esc(initial)}</b></div>'
    )


def table(headers: list[tuple[str, str]], rows: list[str], empty: str) -> str:
    if not rows:
        return f'<p class="muted">{esc(empty)}</p>'
    head = "".join(f'<th class="{cls}" scope="col">{esc(title)}</th>' for title, cls in headers)
    body = "".join(f"<tr>{row}</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def sparkline(series: list[float], width: int = 620, height: int = 120) -> str:
    if len(series) < 2:
        return '<p class="muted">No closed trades yet, so there is no equity curve to draw.</p>'

    low, high = min(series), max(series)
    span = (high - low) or 1.0
    step = width / (len(series) - 1)
    points = [
        (index * step, height - 10 - (value - low) / span * (height - 20))
        for index, value in enumerate(series)
    ]
    line = " ".join(f"{'M' if index == 0 else 'L'}{x:.1f},{y:.1f}" for index, (x, y) in enumerate(points))
    area = f"M0,{height} " + " ".join(f"L{x:.1f},{y:.1f}" for x, y in points) + f" L{width},{height} Z"
    stroke = "#3fb950" if series[-1] >= 0 else "#f85149"
    last_x, last_y = points[-1]

    return (
        f'<svg class="curve" viewBox="0 0 {width} {height}" preserveAspectRatio="none" role="img" '
        f'aria-label="Cumulative realized PnL: {money(series[-1])}">'
        f"<defs><linearGradient id=\"fill\" x1=\"0\" y1=\"0\" x2=\"0\" y2=\"1\">"
        f'<stop offset="0%" stop-color="{stroke}" stop-opacity="0.28"/>'
        f'<stop offset="100%" stop-color="{stroke}" stop-opacity="0"/>'
        f"</linearGradient></defs>"
        f'<path d="{area}" fill="url(#fill)"/>'
        f'<path d="{line}" fill="none" stroke="{stroke}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3.5" fill="{stroke}"/>'
        f'<text x="4" y="12" fill="#8b949e" font-size="10">low {money(low)}</text>'
        f'<text x="{width - 4}" y="12" text-anchor="end" fill="#8b949e" font-size="10">high {money(high)}</text>'
        f"</svg>"
    )


def build(db_path: Path, out_dir: Path) -> None:
    settings = config_mod.load_config()
    chain = settings["chain"]
    connection = store.connect(db_path)
    try:
        store.bootstrap(
            connection,
            float(settings["risk"]["starting_cash_usd"]),
            datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )
        stats = store.totals(connection)
        positions = store.open_positions(connection)
        wins = store.win_stats(connection)
        series = store.realized_series(connection)
        watchlist = store.latest_candidates(connection, 40)
        last_scan = store.last_candidate_time(connection)

        marks: dict[str, dict] = {}
        if positions:
            try:
                marks = market.token_snapshots(chain, [item["token_address"] for item in positions])
            except Exception as exc:  # noqa: BLE001 - marks are cosmetic here
                print(f"mark lookup failed: {exc}")

        position_rows = []
        market_value = 0.0
        for position in positions:
            mark = marks.get(position["token_address"]) or {}
            price = mark.get("price_usd") or position["entry_price"]
            value = float(position["qty"]) * float(price)
            market_value += value
            pnl = value - float(position["entry_usd"])
            pct = (pnl / float(position["entry_usd"]) * 100.0) if float(position["entry_usd"]) else 0.0
            held = (time.time() - float(position["opened_at"])) / 60.0
            position_rows.append(
                f'<td><a href="https://dexscreener.com/{esc(chain)}/{esc(position["token_address"])}" '
                f'target="_blank" rel="noopener">{esc(position["symbol"])}</a></td>'
                f'<td class="num">{money(position["entry_usd"])}</td>'
                f'<td class="num">{price_text(position["entry_price"])}</td>'
                f'<td class="num">{price_text(price)}</td>'
                f'<td class="num">{money(value)}</td>'
                f'<td class="num {pct_class(pnl)}">{money(pnl)} ({pct:+.1f}%)</td>'
                f'<td class="num">{held:.0f}m</td>'
                f'<td class="wide">{esc((position.get("thesis") or "")[:140])}</td>'
            )

        watch_rows = []
        for item in watchlist:
            status = str(item.get("status") or "seen")
            label = STAGE_LABELS.get(status, status)
            change = item.get("change_h24")
            age = item.get("age_minutes")
            watch_rows.append(
                f'<td><a href="https://dexscreener.com/{esc(chain)}/{esc(item["token_address"])}" '
                f'target="_blank" rel="noopener">{esc(item.get("symbol") or "?")}</a></td>'
                f'<td class="num">{number(item.get("score"), 1)}</td>'
                f'<td class="num">{compact(item.get("liquidity_usd"))}</td>'
                f'<td class="num">{compact(item.get("volume_h24"))}</td>'
                f'<td class="num">{number(age, 0) if age is not None else "-"}m</td>'
                f'<td class="num {pct_class(change)}">{number(change, 0)}%</td>'
                f'<td class="num">{int(item.get("corroboration") or 0)}x</td>'
                f'<td><span class="pill {esc(status)}">{esc(label)}</span></td>'
                f'<td class="wide">{esc((item.get("reason") or ""))[:110]}</td>'
            )

        trade_rows = []
        for trade in store.recent_trades(connection, 30):
            when = datetime.fromtimestamp(trade["ts"], timezone.utc).strftime("%m-%d %H:%M")
            pnl = trade.get("pnl_usd")
            pnl_cell = (
                '<td class="num muted">-</td>'
                if pnl is None
                else f'<td class="num {pct_class(pnl)}">{float(pnl):+.2f}</td>'
            )
            side_cls = "up" if trade["side"] == "buy" else "down"
            trade_rows.append(
                f'<td class="muted">{esc(when)}</td>'
                f'<td class="{side_cls}">{esc(trade["side"].upper())}</td>'
                f'<td>{esc(trade["symbol"])}</td>'
                f'<td class="num">{money(trade["usd"])}</td>'
                f'<td class="num">{price_text(trade["price"])}</td>'
                f"{pnl_cell}"
                f'<td class="wide">{esc((trade.get("reason") or "")[:120])}</td>'
            )

        decision_rows = []
        raw_decisions = connection.execute(
            "SELECT ts, symbol, payload FROM decisions WHERE stage = 'analyst' ORDER BY ts DESC LIMIT 20"
        ).fetchall()
        for row in raw_decisions:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                continue
            when = datetime.fromtimestamp(row["ts"], timezone.utc).strftime("%m-%d %H:%M")
            action = str(payload.get("action") or "?")
            action_cls = "up" if action == "buy" else "muted"
            decision_rows.append(
                f'<td class="muted">{esc(when)}</td>'
                f'<td>{esc(row["symbol"] or "?")}</td>'
                f'<td class="{action_cls}">{esc(action)}</td>'
                f'<td class="num">{number(payload.get("score"), 0)}</td>'
                f'<td class="muted">{esc(payload.get("source") or "?")}</td>'
                f'<td class="wide">{esc((payload.get("thesis") or "")[:160])}</td>'
            )

        equity = stats["cash_usd"] + market_value
        unrealized = market_value - sum(float(item["entry_usd"]) for item in positions)
        tint = "#3fb950" if equity >= float(stats["starting_cash_usd"]) else "#f85149"

        if last_scan:
            scanned = datetime.fromtimestamp(last_scan, timezone.utc).strftime("%m-%d %H:%M")
            scan_note = f'<p class="muted">Latest scan {esc(scanned)} UTC · {len(watchlist)} token(s) scored</p>'
        else:
            scan_note = '<p class="muted">No scan recorded yet. The first cycle fills this in.</p>'

        kpis = "".join(
            [
                kpi("Equity", equity, 2, "$"),
                kpi("Unrealized", unrealized, 2, "$", cls=pct_class(unrealized)),
                kpi("Realized today", stats["realized_pnl_today"], 2, "$", cls=pct_class(stats["realized_pnl_today"])),
                kpi("Realized all-time", stats["realized_pnl_total"], 2, "$", cls=pct_class(stats["realized_pnl_total"])),
                kpi("Win rate", wins["win_rate"], 0, "", "%"),
                kpi("Closed trades", wins["closed"], 0),
                kpi("Open positions", len(positions), 0),
            ]
        )

        kill_path = config_mod.kill_switch_path(settings)
        closed_count = wins["closed"]

        curve_html = sparkline(series)
        positions_table = table(
            [
                ("Token", ""),
                ("Entry", "num"),
                ("Entry price", "num"),
                ("Mark", "num"),
                ("Value", "num"),
                ("PnL", "num"),
                ("Held", "num"),
                ("Thesis", ""),
            ],
            position_rows,
            "None open.",
        )
        watch_table = table(
            [
                ("Token", ""),
                ("Score", "num"),
                ("Liquidity", "num"),
                ("Volume 24h", "num"),
                ("Age", "num"),
                ("24h", "num"),
                ("Seen", "num"),
                ("Status", ""),
                ("Why", ""),
            ],
            watch_rows,
            "Nothing scored yet.",
        )
        trades_table = table(
            [
                ("When", ""),
                ("Side", ""),
                ("Token", ""),
                ("USD", "num"),
                ("Price", "num"),
                ("PnL", "num"),
                ("Reason", ""),
            ],
            trade_rows,
            "No trades yet.",
        )
        decisions_table = table(
            [
                ("When", ""),
                ("Token", ""),
                ("Verdict", ""),
                ("Score", "num"),
                ("Source", ""),
                ("Thesis", ""),
            ],
            decision_rows,
            "No verdicts yet.",
        )

        body = f"""
<div class="wrap">
  <header class="hero reveal">
    <div class="orb-wrap">
      <canvas id="orb" data-tint="{tint}" aria-hidden="true"></canvas>
      <div class="orb-fallback" aria-hidden="true"></div>
    </div>
    <div>
      <p class="eyebrow">Paper trading desk</p>
      <h1>CoinPilot</h1>
      <p class="lede">{esc(chain)} · six sub-agents · simulated fills only, no wallet, no keys</p>
      <div class="kpis">{kpis}</div>
    </div>
  </header>

  <section class="card reveal">
    <h2>Equity curve <span class="count">{len(positions)} open</span></h2>
    {curve_html}
  </section>

  <section class="card reveal">
    <h2>Open positions <span class="count">{len(positions)}</span></h2>
    {positions_table}
  </section>

  <section class="card reveal">
    <h2>Watchlist <span class="count">{len(watchlist)}</span></h2>
    {scan_note}
    {watch_table}
  </section>

  <div class="grid two">
    <section class="card reveal">
      <h2>Recent trades <span class="count">{closed_count} closed</span></h2>
      {trades_table}
    </section>

    <section class="card reveal">
      <h2>Analyst verdicts <span class="count">{len(decision_rows)}</span></h2>
      {decisions_table}
    </section>
  </div>

  <p class="foot">
    Generated {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")} UTC ·
    kill switch {'ACTIVE' if kill_path.exists() else 'off'} ·
    paper fills only, nothing is signed
  </p>
</div>
"""
    finally:
        connection.close()

    page = PAGE.replace("%%CSS%%", CSS).replace("%%JS%%", JS).replace("%%BODY%%", body)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(page, encoding="utf-8")
    print(f"dashboard written to {out_dir / 'index.html'}")


def collect(db_path: Path) -> dict:
    """Everything the React dashboard needs, as plain JSON."""

    settings = config_mod.load_config()
    chain = settings["chain"]
    connection = store.connect(db_path)
    try:
        store.bootstrap(
            connection,
            float(settings["risk"]["starting_cash_usd"]),
            datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )
        stats = store.totals(connection)
        positions = store.open_positions(connection)
        wins = store.win_stats(connection)
        watchlist = store.latest_candidates(connection, 40)
        learning = learn.load(connection)
        curve = [
            float(stats["starting_cash_usd"]) + value
            for value in store.realized_series(connection)
        ]

        marks: dict[str, dict] = {}
        if positions:
            try:
                marks = market.token_snapshots(chain, [item["token_address"] for item in positions])
            except Exception as exc:  # noqa: BLE001 - marks are cosmetic
                print(f"mark lookup failed: {exc}")

        position_view = []
        market_value = 0.0
        for position in positions:
            mark = marks.get(position["token_address"]) or {}
            price = mark.get("price_usd") or position["entry_price"]
            value = float(position["qty"]) * float(price)
            market_value += value
            pnl = value - float(position["entry_usd"])
            entry_usd = float(position["entry_usd"])
            position_view.append(
                {
                    "id": position["id"],
                    "symbol": position["symbol"],
                    "token_address": position["token_address"],
                    "entry_usd": entry_usd,
                    "entry_price": float(position["entry_price"]),
                    "mark_price": float(price),
                    "value_usd": value,
                    "pnl_usd": pnl,
                    "pnl_pct": (pnl / entry_usd * 100.0) if entry_usd else 0.0,
                    "held_minutes": (time.time() - float(position["opened_at"])) / 60.0,
                    "thesis": (position.get("thesis") or "")[:400],
                }
            )

        equity = stats["cash_usd"] + market_value
        unrealized = market_value - sum(float(item["entry_usd"]) for item in positions)

        watch_view = [
            {
                "symbol": item.get("symbol") or "?",
                "token_address": item["token_address"],
                "score": float(item.get("score") or 0.0),
                "liquidity": item.get("liquidity_usd"),
                "volume": item.get("volume_h24"),
                "age": item.get("age_minutes"),
                "change": item.get("change_h24"),
                "corroboration": int(item.get("corroboration") or 0),
                "sources": item.get("sources") or "",
                "status": item.get("status") or "seen",
                "reason": (item.get("reason") or "")[:200],
                "credibility": item.get("credibility") or "",
            }
            for item in watchlist
        ]

        trade_view = [
            {
                "ts": trade["ts"],
                "side": trade["side"],
                "symbol": trade["symbol"],
                "usd": float(trade["usd"]),
                "price": float(trade["price"]),
                "pnl": trade.get("pnl_usd"),
                "reason": (trade.get("reason") or "")[:200],
            }
            for trade in store.recent_trades(connection, 40)
        ]

        verdicts = []
        raw = connection.execute(
            "SELECT ts, symbol, payload FROM decisions WHERE stage = 'analyst' ORDER BY ts DESC LIMIT 20"
        ).fetchall()
        for row in raw:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                continue
            verdicts.append(
                {
                    "ts": row["ts"],
                    "symbol": row["symbol"] or "?",
                    "action": str(payload.get("action") or "?"),
                    "score": payload.get("score"),
                    "source": str(payload.get("source") or "?"),
                    "thesis": (payload.get("thesis") or "")[:300],
                    "confidence": payload.get("confidence"),
                    "risks": payload.get("risks") or [],
                }
            )

        return {
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "chain": chain,
            "account": {
                "starting_cash": stats["starting_cash_usd"],
                "cash": stats["cash_usd"],
                "equity": equity,
                "unrealized": unrealized,
                "realized_today": stats["realized_pnl_today"],
                "realized_total": stats["realized_pnl_total"],
                "trades_today": stats["trades_today"],
                "day": stats["day"],
                "kill": config_mod.kill_switch_path(settings).exists(),
            },
            "win_stats": wins,
            "curve": curve,
            "positions": position_view,
            "watchlist": watch_view,
            "trades": trade_view,
            "verdicts": verdicts,
            "learning": learning,
            "last_scan": store.last_candidate_time(connection),
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="dashboard", description="Render the paper portfolio as a static page.")
    parser.add_argument("command", choices=["build", "data"], help="build: static HTML fallback; data: JSON feed for the React dashboard")
    parser.add_argument("--db", default=None, help="path to the SQLite ledger")
    parser.add_argument("--out", default="dist", help="output directory")
    args = parser.parse_args()

    settings = config_mod.load_config()
    db = Path(args.db) if args.db else config_mod.db_path(settings)
    if not db.exists():
        print(f"no ledger at {db}; run the bot first")
        return 1
    if args.command == "data":
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "data.json"
        out_path.write_text(json.dumps(collect(db), default=str), encoding="utf-8")
        print(f"data feed written to {out_path}")
        return 0

    build(db, Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
