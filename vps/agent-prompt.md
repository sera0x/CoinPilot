# Prompt for the CoinPilot VPS agent

This file lives in the repo at `vps/agent-prompt.md` — the agent on the VPS
can read it straight from GitHub
(`https://raw.githubusercontent.com/sera0x/CoinPilot/main/vps/agent-prompt.md`)
or you can copy-paste it from your phone. Before sending, replace the
placeholder on the OPENAI_API_KEY line with your Groq key — ideally a freshly
rotated one (console.groq.com → API Keys → create new, then delete the old).

---

Mission: deploy CoinPilot, a Python paper-trading bot (NO wallet, NO crypto
keys, NO real money — simulated fills only), on this VPS, expose its
dashboard through a cloudflared quick tunnel, and verify everything runs.
Do not modify the bot's strategy code.

STEP 1 — Install
    git clone https://github.com/sera0x/CoinPilot /opt/CoinPilot
    cd /opt/CoinPilot
    bash vps/install.sh
The installer: seeds the ledger from the GitHub "ledger" branch (keeps the
existing paper history and learning state), installs two systemd services —
coinpilot.service (bot loop, full cycle every 2 minutes, MemoryMax=180M) and
dashboard.service (static dashboard on port 8080, MemoryMax=40M) — and adds
a cron that regenerates the dashboard every 2 minutes.

STEP 2 — LLM secrets
    nano /opt/CoinPilot/vps/coinpilot.env
It already has a template. Fill in:
    OPENAI_API_KEY=<my Groq key, gsk_...>
    COINPILOT_LLM_BASE_URL=https://api.groq.com/openai/v1
    COINPILOT_LLM_MODEL=llama-3.3-70b-versatile
Then: chmod 600 /opt/CoinPilot/vps/coinpilot.env
      systemctl restart coinpilot.service
Never commit this file to git.

STEP 3 — Verify the bot
    journalctl -u coinpilot.service -n 40 --no-pager
Expect scout counts, "equity $..." lines, and possibly "source"-style analyst
lines. Also: curl -s localhost:8080 | head -c 300  (dashboard serves).
If Groq returns 401/404, report the exact error text back to me verbatim.

STEP 4 — Cloudflare quick tunnel (no ports opened, no account needed)
    wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
    dpkg -i cloudflared-linux-amd64.deb
Create /etc/systemd/system/cloudflared.service with:
    [Unit]
    Description=Cloudflare tunnel to CoinPilot dashboard
    After=network-online.target
    [Service]
    ExecStart=/usr/local/bin/cloudflared tunnel --url http://localhost:8080
    Restart=always
    RestartSec=10
    MemoryMax=30M
    [Install]
    WantedBy=multi-user.target
Then: systemctl daemon-reload && systemctl enable --now cloudflared
Grab the https://<random>.trycloudflare.com URL from
    journalctl -u cloudflared -n 20 --no-pager
and report it to me — that is the dashboard link.
Note: quick-tunnel URLs change if the tunnel restarts; print the current one
after any restart.

STEP 5 — Health report back to me
    systemctl status coinpilot dashboard cloudflared --no-pager
    systemd-cgtop | grep -i coinpilot   (total RAM should stay under ~200 MB)
Then tell me: the trycloudflare dashboard URL, whether the bot loop is
cycling, and any errors you saw.

RULES: do not open firewall ports (the tunnel replaces that), do not commit
vps/coinpilot.env to git, do not install Docker or anything heavy — this VPS
must keep the whole stack under ~200 MB RAM.
