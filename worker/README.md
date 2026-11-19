# CoinPilot on Cloudflare Workers

The same paper-trading pipeline as the Python version — instructor, scouts,
filter, analyst, risk, paper trader — rewritten in TypeScript as one scheduled
Worker, because Workers run JavaScript and cannot host Python subprocesses or
SQLite files. Storage is **D1** (Cloudflare's SQLite), trading runs on a cron
trigger every 5 minutes, and a browser dashboard ships with it.

## What you get

- **Dashboard** — equity, cash, realized/unrealized PnL, open positions with
  live marks, every token the scan surfaced with its score and status, recent
  trades, an equity curve, and the learning panel. Served at your worker's URL.
- **Self-learning** — every entry stores the raw per-signal score
  contributions it had at buy time. When the trade closes, signals that
  aligned with the outcome gain influence, signals that contradicted it lose
  some. Each group's multiplier is clamped to [0.5, 2.0] so a few trades can
  never rewrite the strategy. All weights are on the dashboard.
- **Kill switch** — the "Kill / Resume" button stops new entries while exits
  continue. Same as the `KILL` file in the Python version.
- **Paper only** — no wallet, no keys, no code path that can sign anything.

## One-time setup

You need a free Cloudflare account and Node on your computer.

```bash
cd worker
npm install
npx wrangler login

npx wrangler d1 create coinpilot
#   ^ prints a database id — paste it into wrangler.toml (database_id)

npx wrangler d1 execute coinpilot --remote --file=schema.sql
npx wrangler deploy
```

`wrangler deploy` prints the URL. Open it: that's the dashboard. The first
cron cycle runs within 5 minutes; "Run cycle" forces one immediately.

## Optional knobs

```bash
npx wrangler secret put LLM_API_KEY      # enables the LLM analyst + instructor
npx wrangler secret put DASHBOARD_TOKEN  # requires ?token=... on the kill/run buttons
```

Without `LLM_API_KEY` everything runs on the rule engine (the data comes from
the public DexScreener API, no key needed). Without `DASHBOARD_TOKEN` the
dashboard is open to anyone who finds the URL — harmless while it is paper
trading, but set the token if you want the buttons to yourself.

To tune the strategy without touching code, edit `CONFIG_JSON` in
`wrangler.toml` — any JSON subset of the Python version's `config.example.json`
deep-merges over the defaults. The worker defaults are deliberately smaller
(`max_addresses: 30`, `max_ticker_lookups: 8`) because Workers' free tier caps
subrequests at 50 per invocation.

## Logs

`npx wrangler tail` streams the cycle logs — every scout failure, decision,
fill and exit, same lines as the Python bot printed.

## Differences from the Python version

- One Worker process with the six agents as modules, not six subprocesses;
  scouts still run in parallel via `Promise.all`.
- D1 instead of a local SQLite file; the kill switch is a flag in the `state`
  table instead of a file.
- Ticker lookups and direct mints are capped lower to respect the free plan's
  50-subrequest budget. Paid plans raise this if you want.
- The Python CLI (`main.py scan`, `selftest.py`, ...) still works unchanged if
  you also want to run locally — the two share nothing at runtime.
