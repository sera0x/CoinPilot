"""Sub-agent: hunt token leads out on the open internet.

One process handles one source so several scouts can run side by side:
  news   - RSS feeds from crypto outlets
  forums - new posts on the meme coin and Solana subreddits
  feeds  - DexScreener's own new-profile and boost streams
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from xml.etree import ElementTree

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coinpilot import agentio, http, market
from coinpilot import config as config_mod

# Solana mints are base58, 32-44 characters.
MINT_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
# Cashtags are the reliable way people flag a ticker: $WIF, $BONK
TICKER_RE = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,11})\b")

ATOM = "{http://www.w3.org/2005/Atom}"
MAX_SUMMARY = 2000


def request_headers(config: dict) -> dict | None:
    """Optional override: some sites 403 anything that is not a browser."""

    agent = str(config["scan"].get("user_agent") or "").strip()
    return {"User-Agent": agent} if agent else None


def extract(text: str) -> tuple[list[str], list[str]]:
    mints = sorted({match for match in MINT_RE.findall(text or "")})
    tickers = sorted({match.upper() for match in TICKER_RE.findall(text or "") if len(match) >= 2})
    return mints, tickers[:8]


def make_lead(source: str, title: str, url: str, body: str = "", published=None) -> dict:
    mints, tickers = extract(f"{title} {body}"[:MAX_SUMMARY])
    return {
        "source": source,
        "title": (title or "").strip()[:200],
        "url": (url or "").strip()[:300],
        "mints": mints,
        "tickers": tickers,
        "published_at": published,
    }


def collect_forums(config: dict) -> list[dict]:
    leads: list[dict] = []
    for subreddit in (config["scan"].get("forum_subreddits") or [])[:8]:
        url = f"https://www.reddit.com/r/{subreddit}/new.json"
        try:
            payload = http.get_json(
                url, params={"limit": 50, "raw_json": 1}, timeout=20, retries=1, headers=request_headers(config)
            )
        except Exception as exc:  # noqa: BLE001 - one blocked forum is not fatal
            agentio.log(f"r/{subreddit} failed: {exc}")
            continue
        children = ((payload or {}).get("data") or {}).get("children") or []
        for child in children:
            if not isinstance(child, dict):
                continue
            data = child.get("data") or {}
            permalink = data.get("permalink") or ""
            leads.append(
                make_lead(
                    "forums",
                    f"r/{subreddit}: {data.get('title') or ''}",
                    f"https://www.reddit.com{permalink}" if permalink else "",
                    data.get("selftext") or "",
                    data.get("created_utc"),
                )
            )
        agentio.log(f"r/{subreddit}: {len(children)} post(s)")
    return leads


def _atom_text(element, tag: str) -> str:
    return (element.findtext(tag) or element.findtext(f"{ATOM}{tag}") or "").strip()


def collect_news(config: dict) -> list[dict]:
    leads: list[dict] = []
    for feed_url in (config["scan"].get("news_feeds") or [])[:8]:
        try:
            raw = http.get_text(feed_url, timeout=20, retries=1, headers=request_headers(config))
        except Exception as exc:  # noqa: BLE001
            agentio.log(f"feed {feed_url} failed: {exc}")
            continue
        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError as exc:
            agentio.log(f"feed {feed_url} is not valid XML: {exc}")
            continue

        items = root.findall(".//item") or root.findall(f".//{ATOM}entry")
        for item in items[:40]:
            title = _atom_text(item, "title")
            link = _atom_text(item, "link")
            if not link:
                atom_link = item.find(f"{ATOM}link")
                if atom_link is not None:
                    link = atom_link.get("href") or ""
            summary = _atom_text(item, "description") or _atom_text(item, "summary")
            leads.append(make_lead("news", title, link, summary, _atom_text(item, "pubDate")))
        agentio.log(f"{feed_url}: {len(items)} item(s)")
    return leads


def collect_feeds(config: dict) -> list[dict]:
    chain = config["chain"]
    leads: list[dict] = []

    collectors = (
        ("new token profile", market.latest_profiles),
        ("boosted token", market.latest_boosts),
    )
    for label, fetch in collectors:
        try:
            items = fetch()
        except Exception as exc:  # noqa: BLE001
            agentio.log(f"{label} source failed: {exc}")
            continue
        kept = 0
        for item in items:
            if item.get("chainId") != chain or not item.get("tokenAddress"):
                continue
            leads.append(
                {
                    "source": "feeds",
                    "title": f"{label}: {(item.get('description') or '')[:120]}".strip(),
                    "url": item.get("url") or "",
                    "mints": [item["tokenAddress"]],
                    "tickers": [],
                    "published_at": None,
                }
            )
            kept += 1
        agentio.log(f"{label}: {kept} on {chain}")

    for slug in (config["scan"].get("metas") or [])[:8]:
        try:
            snapshots = market.meta_snapshots(slug)
        except Exception as exc:  # noqa: BLE001
            agentio.log(f"meta {slug} failed: {exc}")
            continue
        for snap in snapshots:
            if not snap.get("token_address"):
                continue
            leads.append(
                {
                    "source": "feeds",
                    "title": f"meta {slug}: {snap.get('symbol') or ''}",
                    "url": snap.get("url") or "",
                    "mints": [snap["token_address"]],
                    "tickers": [snap["symbol"].upper()] if snap.get("symbol") else [],
                    "published_at": None,
                }
            )
    return leads


COLLECTORS = {"news": collect_news, "forums": collect_forums, "feeds": collect_feeds}


def handler(payload: dict) -> dict:
    config = config_mod.load_config()
    source = str(payload.get("source") or "feeds")
    collector = COLLECTORS.get(source)
    if collector is None:
        raise ValueError(f"unknown source {source!r}; expected one of {sorted(COLLECTORS)}")

    exclude = {token for token in (payload.get("exclude_tokens") or []) if token}
    raw_leads = collector(config)

    leads = []
    for item in raw_leads:
        mints = [mint for mint in item.get("mints") or [] if mint not in exclude]
        if not mints and not item.get("tickers"):
            continue
        leads.append({**item, "mints": mints})

    limit = int(config["scan"].get("max_leads") or 120)
    return {
        "ok": True,
        "source": source,
        "leads": leads[:limit],
        "scanned": len(raw_leads),
        "directive": payload.get("directive") or "",
    }


if __name__ == "__main__":
    agentio.serve(handler)
