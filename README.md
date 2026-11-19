# CoinPilot

A tiny **paper-trading** agent for Solana meme coins. It watches the public
DEX Screener feeds, scores what it finds, asks a language model for a second
opinion, and books simulated buys and sells into a local SQLite portfolio.

No wallet. No private keys. No real orders. Nothing is ever signed or sent to
a chain.

## The agents

Six sub-agents, each a separate process with one job:

| Agent | Job |
| --- | --- |
| **instructor** | Reads the portfolio and sets the plan for the cycle: which sources to run, how many candidates to keep, how many entries to attempt, the one-line directive every agent downstream must respect, and a posture of normal, defensive or paused. |
| **scout** (one process per source) | Hunts leads. `news` reads crypto RSS feeds, `forums` reads new posts on the meme coin subreddits, `feeds` reads DexScreener's new-profile and boost streams. Pulls out cashtags and raw mint addresses and keeps the link each came from. |
| **filter** | Resolves leads into real prices, merges duplicates, counts how many independent places mentioned a token, applies every hard screen, and scores what survives. Then a RugCheck credibility pass and a whale-concentration pass (Solana public RPC: top ex-pool wallets) run over the shortlist head, and every screen-passing token is written to the archive. |
| **analyst** | Judgment. Sends one snapshot to the model for a buy/skip verdict with a confidence, a thesis and the risks. Falls back to the rule score when the model is unavailable. |
| **risk** | Refuses anything outside the limits, and decides the size. |
| **trader** | The only process that writes: paper fills through the broker. |
| **auditor** | Runs after every cycle. Grades the bot's own results against the archive (what happened to tokens it priced at/above the entry bar) and against realized trades, and moves `buy_score` when the evidence says the bar is wrong — bounded to +10/-6 from the config base, one 2-point step at a time, at most once per 3 hours. Every change is logged with its evidence, shown on the dashboard. |

Exits are handled by the orchestrator every cycle — stop loss, take profit,
trailing stop, time expiry — because a decision that must always happen should
not depend on another agent answering.

### Strategy choice

"Buy new launches by filter" is the usual meme-coin entry, but it is also the
easiest way to fill a paper account with honeypot-shaped garbage. CoinPilot
instead uses **filtered momentum**: liquidity and turnover floors, 24h trade
count, an age window (too new is penalised, decades-old stories are stale), 1h
buy pressure, 1h/6h momentum, and a penalty for entries already up hundreds of
percent in a day. A token mentioned in more than one place scores higher, and
the analyst is there to reject setups the arithmetic likes but that read badly
— not to invent trades.

Tune it in `config.json` under `scan` and `strategy`.

### How it learns

Three loops, in increasing order of ambition:

1. **Signal weights** — every entry stores the raw per-signal score contributions it had at buy time. When the trade closes, signals that aligned with the outcome gain influence, signals that contradicted it lose some, each clamped to ×0.5–×2.0 of base.
2. **The archive** — every screen-passing token is remembered as a *sighting* (price, score, age, corroboration). Study passes re-price old sightings and compute what the token did after the bot saw it. Aggregated by score band that becomes the edge table: "tokens we rated 70+ averaged X% since sighting". Resolved sightings older than 21 days are pruned.
3. **The auditor** — compares the archive's hindsight against the current entry bar. If tokens above the bar have historically gone nowhere, it raises `buy_score`; if they did well and the bar was raised by earlier losses, it eases back. Overrides live in the ledger and flow through `load_config`, so every agent trades on the audited threshold. Hard bounds and a 3-hour cooldown keep a bad inference from running away with the strategy.

Pop culture and money flow enter the same way news does: RSS feeds (extend `scan.news_feeds`) and the whale-flow pass (`scan.rpc_url` points at any Solana RPC if the public one rate-limits).

## Architecture

```
main.py (orchestrator, no trading logic)
  |
  +-- instructor                 plan: sources, budgets, posture, directive
  |
  +-- scout (news)    \
  +-- scout (forums)   > run side by side, one process per source
  +-- scout (feeds)   /
  |
  +-- filter                     leads -> prices -> screens -> scored candidates
  +-- analyst   (per entry)      judgment: buy/skip, score, thesis, risks
  +-- risk      (per entry)      limits, then size
  +-- trader    (per entry)      paper fill into SQLite
```

Sub-agents are separate `python3` processes talking JSON on stdin/stdout. They
share no memory, no event loop and no framework. Each one is a plain
interpreter plus a few hundred lines of standard library code — no model
weights, no torch, no numpy. Expect roughly 20–40 MB resident per process,
comfortably inside a 500 MB per-agent budget. Diagnostics go to stderr; stdout
carries exactly one JSON object.

## Requirements

- Python 3.9 or newer
- Standard library only, no `pip install` needed
- Optional: an API key for any OpenAI-compatible endpoint, for the analyst

The data comes from the public DexScreener API, which needs no key, plus
whatever public RSS feeds and subreddits are listed in `config.json` — all
read-only, no accounts.

## Quick start

```bash
cd CoinPilot
cp config.example.json config.json     # optional, the example is used otherwise
export OPENAI_API_KEY=sk-...           # optional: enables the LLM analyst

python3 selftest.py                     # offline checks: rules, paper fills, risk limits
python3 main.py doctor                 # check config, feeds, database
python3 main.py scan                   # print scored candidates, trade nothing
python3 main.py once                   # one full cycle
python3 main.py run --interval 300     # loop forever, 5 minutes apart
python3 main.py status                 # portfolio, open positions, recent trades
python3 main.py reset --yes            # wipe the paper account and start over
```

`config.json` is gitignored; `config.example.json` is the reference.

## Using a different model

Any endpoint that speaks `POST /chat/completions` works:

```json
"llm": {
  "enabled": true,
  "base_url": "https://api.openai.com/v1",
  "model": "gpt-4o-mini",
  "api_key_env": "OPENAI_API_KEY",
  "max_candidates_per_cycle": 3
}
```

Point `base_url` at a local llama.cpp server or OpenRouter and the bot does not
care. `api_key_env` is the *name* of a shell variable, never a key written into
a file. Set `"enabled": false` to run rules-only with no network calls besides
market data.

## Configuration that matters

| Key | Meaning |
| --- | --- |
| `scan.sources` | Which scouts run: any of `news`, `forums`, `feeds` |
| `scan.forum_subreddits` | Subreddits the forums scout reads (`new.json`) |
| `scan.news_feeds` | RSS or Atom feeds the news scout reads |
| `scan.ignore_tickers` | Tickers never treated as a meme coin (majors, stables) |
| `scan.max_leads` | Hard cap on leads per scout, to stay polite |
| `scan.max_ticker_lookups` | How many cashtags get resolved per cycle |
| `scan.min_liquidity_usd` | Hard floor; thin pairs are skipped, not scored |
| `scan.min_volume_24h_usd` | Hard floor on 24h volume |
| `scan.min_age_minutes` / `max_age_hours` | Age window; very new tokens are risky, very old ones are stale |
| `strategy.buy_score` | Minimum score before the risk agent will consider a buy |
| `strategy.position_usd` | Base size per trade, then clamped by the limits below |
| `strategy.take_profit_pct` / `stop_loss_pct` | Exit thresholds |
| `strategy.trailing_stop_pct` | Exit after this much drawdown from the position's peak |
| `strategy.max_hold_minutes` | Time exit |
| `risk.max_position_pct_of_equity` | Cap per position |
| `risk.max_exposure_pct` | Cap on total invested at once |
| `risk.max_position_pct_of_liquidity` | Keeps a paper fill from being absurdly large for the pool |
| `risk.max_daily_loss_usd` | Stop trading for the day at this realized loss |
| `risk.max_trades_per_day` | Hard trade cap |
| `risk.cooldown_minutes` | No re-entry into the same token for this long |
| `paper.fee_pct` / `slippage_pct` | Costs applied to both sides of every fill |

## Kill switch

Create a file named `KILL` in the project root (or whatever
`risk.kill_switch_file` says). The next cycle stops opening new positions and
still manages exits, so you can flatten without it buying anything. Delete the
file to resume.

## Honest limits

- Fills are simulated against the last DexScreener price with a flat slippage
  assumption. Real fills on a moving meme coin can be much worse, and paper
  results are always optimistic.
- RugCheck covers mint/freeze authority and its risk flags, and the whale-flow
  pass measures top ex-pool wallet concentration over the public Solana RPC —
  but only for the shortlist head, and only when the public RPC answers. There
  is still no full holder-concentration or honeypot-simulation check. A paper
  fill in a scam token looks like a good trade; a real one would not.
- The auditor changes its own parameters within hard bounds (+10/-6 on
  buy_score, one 2-point step, one change per 3 hours). That is self-tuning,
  not magic: it reads its own paper results, which are always optimistic.
- Six agents sharing one strategy over public data. This is a research toy, not a
  production trading system, and nothing here is financial advice.
- `mode` must stay `paper`. There is no live broker, and no code path that
  could sign a transaction.

## Running it somewhere

The bot needs a Python interpreter, so it does not run inside the phone app:
run it on a computer, a Raspberry Pi or a small VPS. A scheduled job (cron,
systemd timer, GitHub Actions) calling `python3 main.py once` is enough — the
database is a single SQLite file you can copy anywhere.

### GitHub Actions (no other account needed)

`.github/workflows/coinpilot.yml` runs `python3 main.py once` every 5 minutes
on GitHub's servers (nothing to host, no VPS), keeps the SQLite ledger on a
`ledger` branch (it survives runs), and builds a static dashboard every run.

Seeing the dashboard:

- **Public repo:** Settings → Pages → Source "GitHub Actions", then Settings →
  Secrets and variables → Actions → Variables → New, name `ENABLE_PAGES`,
  value `on`. The workflow then publishes to `https://<user>.github.io/CoinPilot/`
  on every cycle. The repo is public, so the bot's paper ledger is public too.
- **Private repo (or no Pages):** download the `dashboard` artifact from any
  run in the Actions tab (the run's page, under Artifacts), unzip it and open
  `index.html` in a browser.

### The dashboard

A Vite + React + TypeScript app in `dashboard/`, built by the workflow into
`dist/` and published to Pages:

- **Agent swarm:** a three.js particle simulation — one glowing node per
  sub-agent (instructor, three scouts, filter, analyst, risk, trader) with
  work particles flowing between them. Traffic speed and colour follow the
  live portfolio: green while equity ≥ start, red below, faster traffic when
  trades are hot. Falls back to a slower drift under reduced-motion.
- **Grain:** an animated SVG-turbulence film overlay, one data URI.
- **Motion** (motion.dev): count-up KPIs, card reveals.
- **Data:** equity curve, open positions with live marks, the watchlist with
  score, liquidity, volume, age, corroboration, RugCheck credibility, whale
  flow and status, recent trades, analyst verdicts, the self-learning panel —
  the signal weights the bot has earned from its own closed trades — and the
  self-audit panel: the archive's hindsight edge table plus every parameter
  change the auditor made, with the evidence that justified it.

The page fetches `./data.json`, regenerated by `python3 dashboard.py data` on
every cycle. If the npm build ever fails, the workflow falls back to a
dependency-free static render (`dashboard.py build`) so the page never goes
dark. The ledger branch mirrors `site/index.html` + `site/data.json`, viewable
at `https://raw.githack.com/sera0x/CoinPilot/ledger/site/index.html` even if
Pages is down.

Other setup in the repo on GitHub:

1. Optional: Settings → Secrets and variables → Actions → add
   `OPENAI_API_KEY` to turn on the LLM analyst and instructor
2. Kill switch: commit an empty file named `KILL` to the default branch
   (Add file → Create new file → `KILL`). The next cycle exits only.

Know before you lean on it: GitHub's scheduler can run minutes late, and
scheduled workflows on a repo with no activity for 60 days are disabled
(re-enable them under the Actions tab). Every cycle commits the ledger to the
`ledger` branch, so that branch grows quickly; wipe it whenever you like —
the next run recreates it.

### Cloudflare Workers deployment

See [worker/README.md](worker/README.md). It is the same pipeline ported to
TypeScript with D1 storage, a real cron trigger, a live browser dashboard
(tokens, PnL, entries) and self-learning signal weights — tighter schedules
and no scheduler delays, at the cost of a free Cloudflare account.
