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
| **filter** | Resolves leads into real prices, merges duplicates, counts how many independent places mentioned a token, applies every hard screen, and scores what survives. |
| **analyst** | Judgment. Sends one snapshot to the model for a buy/skip verdict with a confidence, a thesis and the risks. Falls back to the rule score when the model is unavailable. |
| **risk** | Refuses anything outside the limits, and decides the size. |
| **trader** | The only process that writes: paper fills through the broker. |

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
- There is no honeypot, mint-authority, freeze-authority or holder-concentration
  check. A paper fill in a scam token looks like a good trade; a real one would
  not.
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

One self-contained `dist/index.html`, built by `dashboard.py` from the ledger.
Content is rendered server-side, so it reads fine with JavaScript blocked; the
visual layer is progressive:

- **Orb:** a WebGL shader sphere (three.js, loaded from a CDN) whose colour
  tracks equity against the starting balance. No WebGL means a CSS orb is
  shown instead.
- **Grain:** an animated SVG turbulence overlay, ffmpeg-free and one data URI.
- **Motion:** count-up numbers, staggered table rows and card reveals via
  Motion (motion.dev) if the CDN answers, with a CSS/IntersectionObserver
  reveal as the fallback.
- **Data:** equity curve from closed trades, open positions with live marks,
  the latest scan's watchlist with score, liquidity, volume, age, how many
  sources mentioned each token and what happened to it (entered, blocked,
  skipped), recent trades, and the analyst's latest verdicts.

React Bits is a React component collection and this page has no build step or
React runtime, so its effects are reproduced in plain CSS and Motion rather
than imported. If you want the literal library, the dashboard needs a Vite +
React build in the workflow; ask and I'll add it.

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
