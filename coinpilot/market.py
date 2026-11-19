"""Market data from the public DexScreener API (no key required)."""

from __future__ import annotations

import time

from . import http

BASE_URL = "https://api.dexscreener.com"


def now_ms() -> int:
    return int(time.time() * 1000)


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pairs(payload) -> list[dict]:
    """Endpoints return either a list of pairs or an object holding them."""

    if payload is None:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        pairs = payload.get("pairs")
        if isinstance(pairs, list):
            return [item for item in pairs if isinstance(item, dict)]
        return [payload]
    return []


def _window(values, key: str):
    if not isinstance(values, dict):
        return None
    return _num(values.get(key))


def snapshot(pair: dict) -> dict:
    """Flatten one DexScreener pair into the fields the strategy cares about."""

    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    liquidity = pair.get("liquidity") or {}
    txns = pair.get("txns") or {}
    volume = pair.get("volume") or {}
    change = pair.get("priceChange") or {}
    info = pair.get("info") or {}
    boosts = pair.get("boosts") or {}

    created_at = _num(pair.get("pairCreatedAt"))
    age_minutes = None
    if created_at:
        age_minutes = max(0.0, (now_ms() - created_at) / 60000.0)

    def tx(window: str, side: str):
        window_data = txns.get(window) if isinstance(txns, dict) else None
        if not isinstance(window_data, dict):
            return None
        return _num(window_data.get(side))

    return {
        "chain": pair.get("chainId"),
        "dex": pair.get("dexId"),
        "token_address": base.get("address"),
        "symbol": base.get("symbol"),
        "name": base.get("name"),
        "quote_symbol": quote.get("symbol"),
        "pair_address": pair.get("pairAddress"),
        "price_usd": _num(pair.get("priceUsd")),
        "price_native": _num(pair.get("priceNative")),
        "liquidity_usd": _num(liquidity.get("usd")),
        "fdv": _num(pair.get("fdv")),
        "market_cap": _num(pair.get("marketCap")),
        "volume_m5": _window(volume, "m5"),
        "volume_h1": _window(volume, "h1"),
        "volume_h6": _window(volume, "h6"),
        "volume_h24": _window(volume, "h24"),
        "change_m5": _window(change, "m5"),
        "change_h1": _window(change, "h1"),
        "change_h6": _window(change, "h6"),
        "change_h24": _window(change, "h24"),
        "buys_m5": tx("m5", "buys"),
        "sells_m5": tx("m5", "sells"),
        "buys_h1": tx("h1", "buys"),
        "sells_h1": tx("h1", "sells"),
        "buys_h24": tx("h24", "buys"),
        "sells_h24": tx("h24", "sells"),
        "pair_created_at": created_at,
        "age_minutes": age_minutes,
        "boosts_active": _num(boosts.get("active")),
        "socials": [social.get("platform") for social in info.get("socials") or [] if isinstance(social, dict)],
        "url": pair.get("url"),
    }


def token_snapshots(chain: str, addresses: list[str]) -> dict[str, dict]:
    """Deepest pair per token address."""

    unique = [address for address in dict.fromkeys(addresses) if address]
    best: dict[str, dict] = {}
    for start in range(0, len(unique), 30):
        chunk = unique[start : start + 30]
        url = f"{BASE_URL}/tokens/v1/{chain}/{','.join(chunk)}"
        for pair in _pairs(http.get_json(url)):
            snap = snapshot(pair)
            token = snap.get("token_address")
            if not token:
                continue
            current = best.get(token)
            if current is None or (snap.get("liquidity_usd") or 0) > (current.get("liquidity_usd") or 0):
                best[token] = snap
    return best


def token_snapshot(chain: str, address: str) -> dict | None:
    return token_snapshots(chain, [address]).get(address)


def token_pairs(chain: str, address: str) -> list[dict]:
    url = f"{BASE_URL}/token-pairs/v1/{chain}/{address}"
    return [snapshot(pair) for pair in _pairs(http.get_json(url))]


def search_pairs(query: str) -> list[dict]:
    payload = http.get_json(f"{BASE_URL}/latest/dex/search", params={"q": query})
    return [snapshot(pair) for pair in _pairs(payload)]


def latest_profiles() -> list[dict]:
    payload = http.get_json(f"{BASE_URL}/token-profiles/latest/v1")
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def recent_profiles() -> list[dict]:
    payload = http.get_json(f"{BASE_URL}/token-profiles/recent-updates/v1")
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def latest_boosts() -> list[dict]:
    payload = http.get_json(f"{BASE_URL}/token-boosts/latest/v1")
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def trending_metas() -> list[dict]:
    payload = http.get_json(f"{BASE_URL}/metas/trending/v1")
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def meta(slug: str) -> dict:
    payload = http.get_json(f"{BASE_URL}/metas/meta/v1/{slug}")
    return payload if isinstance(payload, dict) else {}


def meta_snapshots(slug: str) -> list[dict]:
    payload = meta(slug)
    pairs = payload.get("pairs")
    if not isinstance(pairs, list):
        return []
    return [snapshot(pair) for pair in pairs if isinstance(pair, dict)]
