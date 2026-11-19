#!/usr/bin/env python3
"""Build the static dashboard (one HTML file) from the paper SQLite ledger.

Used by the GitHub Actions workflow: the bot runs a cycle, this renders the
state into dist/index.html, and the artifact is deployed to GitHub Pages.
"""

from __future__ import annotations

import argparse
import html
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from coinpilot import config as config_mod
from coinpilot import market, store

CSS = """
  body { margin:0; background:#0d1117; color:#c9d1d9; font:14px/1.45 -apple-system,'Segoe UI',Roboto,sans-serif; }
  header { display:flex; flex-wrap:wrap; gap:16px; align-items:center; padding:14px 20px; border-bottom:1px solid #21262d; background:#161b22; }
  h1 { font-size:18px; margin:0; } h1 small { color:#8b949e; font-weight:400; }
  .stat b { display:block; font-size:15px; } .stat span { color:#8b949e; font-size:11px; text-transform:uppercase; letter-spacing:.04em; }
  .up { color:#3fb950; } .down { color:#f85149; } .muted { color:#8b949e; }
  main { padding:16px 20px 40px; } section { background:#161b22; border:1px solid #21262d; border-radius:10px; padding:14px 16px; margin-bottom:16px; overflow:auto; }
  h2 { margin:0 0 10px; font-size:13px; text-transform:uppercase; letter-spacing:.05em; color:#8b949e; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th { text-align:left; color:#8b949e; font-weight:500; padding:4px 8px; border-bottom:1px solid #21262d; }
  td { padding:6px 8px; border-bottom:1px solid #21262d; white-space:nowrap; }
  a { color:#58a6ff; text-decoration:none; }
"""

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CoinPilot — paper trading desk</title><style>{css}</style></head>
<body>
<header>
  <h1>CoinPilot <small>paper trading · {chain} · GitHub Actions cron</small></h1>
  <div class="stat"><b>{equity}</b><span>Equity</span></div>
  <div class="stat"><b>{cash}</b><span>Cash</span></div>
  <div class="stat"><b class="{cls_today}">{pnl_today}</b><span>Realized today</span></div>
  <div class="stat"><b class="{cls_total}">{pnl_total}</b><span>Realized all-time</span></div>
  <div class="stat"><b class="{cls_unreal}">{unreal}</b><span>Unrealized</span></div>
  <div class="stat"><b>{trades_today}</b><span>Trades today</span></div>
  <div class="stat"><b>{kill}</b><span>Kill switch</span></div>
</header>
<main>
  <section><h2>Open positions</h2>{positions}</section>
  <section><h2>Recent trades</h2>{trades}</section>
  <section><h2>Latest analyst verdicts</h2>{decisions}</section>
  <p class="muted">Generated {generated} UTC · paper fills only, no wallet, no keys.</p>
</main>
</body></html>"""


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def money(value) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def price_text(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "?"
    if number <= 0:
        return "0"
    if number < 0.0001:
        return f"{number:.10f}"
    if number < 1:
        return f"{number:.6f}"
    return f"{number:,.4f}"


def table(headers: list[str], rows: list[str], empty: str) -> str:
    if not rows:
        return f'<p class="muted">{esc(empty)}</p>'
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join(f"<tr>{row}</tr>" for row in rows)
    return f'<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def build(db_path: Path, out_dir: Path) -> None:
    settings = config_mod.load_config()
    connection = store.connect(db_path)
    try:
        store.bootstrap(connection, float(settings["risk"]["starting_cash_usd"]), datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        stats = store.totals(connection)
        positions = store.open_positions(connection)

        marks: dict[str, dict] = {}
        if positions:
            try:
                marks = market.token_snapshots(settings["chain"], [p["token_address"] for p in positions])
            except Exception as exc:  # noqa: BLE001 - marks are cosmetic here
                print(f"mark lookup failed: {exc}")

        rows = []
        market_value = 0.0
        for position in positions:
            mark = marks.get(position["token_address"]) or {}
            price = mark.get("price_usd") or position["entry_price"]
            value = float(position["qty"]) * float(price)
            market_value += value
            pnl = value - float(position["entry_usd"])
            pct = (pnl / float(position["entry_usd"]) * 100.0) if float(position["entry_usd"]) else 0.0
            held = (time.time() - float(position["opened_at"])) / 60.0
            cls = "up" if pnl >= 0 else "down"
            rows.append(
                f'<td><a href="https://dexscreener.com/{settings["chain"]}/{esc(position["token_address"])}" target="_blank">{esc(position["symbol"])}</a></td>'
                f"<td>{money(position['entry_usd'])}</td><td>{price_text(price)}</td><td>{money(value)}</td>"
                f'<td class="{cls}">{money(pnl)} ({pct:+.1f}%)</td><td>{held:.0f}m</td>'
                f'<td class="muted">{esc((position.get("thesis") or "")[:120])}</td>'
            )
        unrealized = market_value - sum(float(p["entry_usd"]) for p in positions)
        equity = stats["cash_usd"] + market_value

        trade_rows = []
        for trade in store.recent_trades(connection, 40):
            when = datetime.fromtimestamp(trade["ts"], timezone.utc).strftime("%m-%d %H:%M")
            pnl = "" if trade.get("pnl_usd") is None else f'<td class="{"up" if trade["pnl_usd"] >= 0 else "down"}">{trade["pnl_usd"]:+.2f}</td>'
            if trade.get("pnl_usd") is None:
                pnl = "<td class='muted'>—</td>"
            trade_rows.append(
                f'<td class="muted">{esc(when)}</td><td>{esc(trade["side"].upper())}</td><td>{esc(trade["symbol"])}</td>'
                f"<td>{money(trade['usd'])}</td><td>{price_text(trade['price'])}</td>{pnl}"
                f'<td class="muted">{esc((trade.get("reason") or "")[:140])}</td>'
            )

        decision_rows = []
        raw_decisions = connection.execute(
            "SELECT ts, symbol, stage, payload FROM decisions WHERE stage = 'analyst' ORDER BY ts DESC LIMIT 15"
        ).fetchall()
        for row in raw_decisions:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                continue
            when = datetime.fromtimestamp(row["ts"], timezone.utc).strftime("%m-%d %H:%M")
            action_cls = "up" if payload.get("action") == "buy" else "muted"
            decision_rows.append(
                f'<td class="muted">{esc(when)}</td><td>{esc(row["symbol"] or "?")}</td>'
                f'<td class="{action_cls}">{esc(payload.get("action"))} {payload.get("score", "?")}</td>'
                f'<td>{esc(payload.get("source", "?"))}</td><td>{esc((payload.get("thesis") or "")[:160])}</td>'
            )

        cls_today = "up" if stats["realized_pnl_today"] >= 0 else "down"
        cls_total = "up" if stats["realized_pnl_total"] >= 0 else "down"
        cls_unreal = "up" if unrealized >= 0 else "down"
        kill_path = config_mod.kill_switch_path(settings)

        page = PAGE.format(
            css=CSS,
            chain=esc(settings["chain"]),
            equity=money(equity),
            cash=money(stats["cash_usd"]),
            pnl_today=money(stats["realized_pnl_today"]),
            pnl_total=money(stats["realized_pnl_total"]),
            unreal=money(unrealized),
            trades_today=stats["trades_today"],
            kill="ACTIVE" if kill_path.exists() else "off",
            positions=table(
                ["Token", "Entry $", "Mark", "Value", "PnL", "Held", "Thesis"], rows, "none open"
            ),
            trades=table(
                ["When", "Side", "Token", "USD", "Price", "PnL", "Reason"], trade_rows, "no trades yet"
            ),
            decisions=table(
                ["When", "Token", "Verdict", "Source", "Thesis"], decision_rows, "no verdicts yet"
            ),
            generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        )
    finally:
        connection.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(page, encoding="utf-8")
    print(f"dashboard written to {out_dir / 'index.html'}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="dashboard", description="Render the paper portfolio as a static page.")
    parser.add_argument("command", choices=["build"])
    parser.add_argument("--db", default=None, help="path to the SQLite ledger")
    parser.add_argument("--out", default="dist", help="output directory")
    args = parser.parse_args()

    settings = config_mod.load_config()
    db = Path(args.db) if args.db else config_mod.db_path(settings)
    if not db.exists():
        print(f"no ledger at {db}; run the bot first")
        return 1
    build(db, Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
