#!/usr/bin/env bash
# CoinPilot VPS installer. Run from inside a fresh clone on the VPS:
#   git clone https://github.com/sera0x/CoinPilot /opt/CoinPilot
#   cd /opt/CoinPilot
#   bash vps/install.sh
# Then edit /opt/CoinPilot/vps/coinpilot.env with your Groq key.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "run as root (sudo bash vps/install.sh)"; exit 1
fi
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
echo "==> installing to ${ROOT}"

# 1. Python (most images already have it)
command -v python3 >/dev/null || apt-get update && apt-get install -y python3

# 2. Seed the ledger from GitHub so the paper history stays continuous
if [[ ! -f data/coinpilot.db ]]; then
  git fetch origin ledger --depth=1 || true
  if git show-ref --verify -q refs/remotes/origin/ledger; then
    git checkout origin/ledger -- data/coinpilot.db
    echo "==> ledger seeded from the GitHub ledger branch"
  else
    echo "==> no ledger on GitHub yet; starting a fresh $1000 paper account"
  fi
fi

# 3. Config (defaults are fine; llm comes from the env file)
[[ -f config.json ]] || cp config.example.json config.json

# 4. Secrets file (NEVER commit this)
if [[ ! -f vps/coinpilot.env ]]; then
  cp vps/coinpilot.env.example vps/coinpilot.env
  echo "==> created vps/coinpilot.env — EDIT IT: paste your Groq key, then:"
fi
chmod 600 vps/coinpilot.env 2>/dev/null || true

# 5. systemd services: the bot loop (full cycle every 2 min) + static dashboard
cp vps/coinpilot.service vps/dashboard.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now coinpilot.service dashboard.service

# 6. Dashboard regeneration every 2 minutes
CRON_LINE='*/2 * * * * cd '"${ROOT}"' && python3 dashboard.py build --out dist >/dev/null 2>&1 && python3 dashboard.py data --out dist >/dev/null 2>&1'
( crontab -l 2>/dev/null | grep -v CoinPilot ; echo "${CRON_LINE}" ) | crontab -

echo "==> done. Live files:"
echo "    bot:       journalctl -u coinpilot.service -f"
echo "    dashboard: http://<this-vps-ip>:8080  (open port 8080 in the host firewall)"
echo "    kill:      touch ${ROOT}/KILL   resume: rm ${ROOT}/KILL"
echo "==> next: disable the two GitHub Actions workflows so two bots don't trade."
