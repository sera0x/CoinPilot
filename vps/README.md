# CoinPilot on a small VPS

The whole pipeline — instructor, scouts, filter (RugCheck + whale flow),
analyst, risk, trader, auditor, archive — runs as **one Python process loop**
with a tiny static file server for the dashboard. Two systemd services, one
cron line. No Docker, no Node on the server, nothing to compile.

## Memory budget (a 512 MB or smaller VPS is fine)

| State | RAM |
| --- | --- |
| Idle between cycles | ~15–25 MB (one Python process) |
| During discovery (~30–60 s per 2 min) | ~100–170 MB peak (main + 3 scouts in parallel) |
| Dashboard server | hard-capped at 40 MB (`MemoryMax`) |
| Bot service hard cap | 180 MB (`MemoryMax`, restart-on-breach) |

Typical live footprint is 50–80 MB; worst case ~170 MB during the discovery
minute. If you want the peak strictly under 100 MB, set `"sources":
["feeds"]` in `config.json` — the feeds scout is the productive one anyway,
and peak drops to ~70 MB. The SQLite ledger stays a few MB forever.

## The VPS advantages you get for free

- **A real clock.** `run --interval 120` means a full cycle every 2 minutes:
  exits (stop loss, take profit, trailing, time) re-checked every 2 minutes,
  no GitHub scheduler roulette, no 60-day disable rule.
- **A dedicated IP.** DexScreener's rate budget is all yours, and the Solana
  RPC (whale pass) will very likely answer — it 403s GitHub's shared pools,
  not your VPS. Reddit may still 403 (also datacenter); test with
  `curl -A "Mozilla/5.0" https://www.reddit.com/r/memecoins/new.json`.

## Install (5 minutes)

```bash
git clone https://github.com/sera0x/CoinPilot /opt/CoinPilot
cd /opt/CoinPilot
bash vps/install.sh
nano vps/coinpilot.env          # paste your Groq key
systemctl restart coinpilot.service
```

The installer seeds the ledger from the GitHub `ledger` branch so the paper
history and learning state carry over, installs both services, and sets the
dashboard regeneration cron. LLM config comes from `vps/coinpilot.env`
(base URL, model, key — values are stripped of stray spaces by the bot, so
paste artifacts can't break it).

## What to do after it's live

1. **Disable the two GitHub workflows** (Actions tab → each workflow → ⋯ →
   Disable workflow). Otherwise two bots trade two separate paper accounts
   and the GitHub dashboard shows the stale one.
2. Open port **8080** in your host's firewall → dashboard at
   `http://<vps-ip>:8080`. It's the static render (all panels, no particle
   swarm — that's the Actions build), regenerated every 2 minutes by cron.
   HTTP, not HTTPS — fine for a paper dashboard with no secrets in it.
3. Watch it: `journalctl -u coinpilot.service -f`
4. **Kill switch**: `touch /opt/CoinPilot/KILL` (exits only, no new entries).
   Resume: `rm /opt/CoinPilot/KILL`.
5. Reset the paper account: `rm data/coinpilot.db* && systemctl restart
   coinpilot.service`.

## Updating

```bash
cd /opt/CoinPilot && git pull && systemctl restart coinpilot.service
```

Keep `vps/coinpilot.env` out of any commit — it holds your Groq key.
