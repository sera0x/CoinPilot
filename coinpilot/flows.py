"""On-chain whale concentration for the shortlist head.

Uses the public Solana RPC (getTokenLargestAccounts + getAccountInfo) —
no key, read-only. Answers the money-flow question the price snapshot
cannot: how much of the supply sits in few wallets, outside the liquidity
pool itself (the single largest token account is almost always the pool,
so it is excluded and concentration is measured over the next ten).

A token whose top ex-pool wallets hold most of the float is one coordinated
dump away from zero, so concentration caps the score the way RugCheck does.
A failed RPC degrades to "unavailable" and never blocks a cycle.
"""

from __future__ import annotations

from coinpilot import http

DEFAULT_RPC = "https://api.mainnet-beta.solana.com"


def _rpc(url: str, method: str, params: list, timeout: int = 10):
    payload = http.post_json(
        url.rstrip("/"),
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=timeout,
        retries=1,
    )
    if payload.get("error"):
        raise RuntimeError(str(payload["error"])[:120])
    return payload.get("result")


def check(mint: str, rpc_url: str = DEFAULT_RPC) -> dict:
    """Return {ok, reject, adjust, note, top10_pct}."""

    try:
        largest = _rpc(rpc_url, "getTokenLargestAccounts", [mint])
        mint_info = _rpc(rpc_url, "getAccountInfo", [mint, {"encoding": "jsonParsed"}])
    except Exception as exc:  # noqa: BLE001 - an outage must not stop the filter
        return {
            "ok": False,
            "reject": None,
            "adjust": 0.0,
            "note": f"flows unavailable ({str(exc)[:60]})",
            "top10_pct": None,
        }

    amounts: list[float] = []
    for entry in (largest or {}).get("value") or []:
        try:
            amounts.append(float(entry.get("uiAmount") or 0))
        except (TypeError, ValueError):
            continue

    parsed = ((mint_info or {}).get("value") or {}).get("data") or {}
    info = parsed.get("parsed") or {}
    details = info.get("info") or {}
    try:
        decimals = int(details.get("decimals") or 6)
    except (TypeError, ValueError):
        decimals = 6
    try:
        supply = float(details.get("supply") or 0) / (10 ** decimals)
    except (TypeError, ValueError):
        supply = 0.0

    if not amounts or supply <= 0:
        return {"ok": True, "reject": None, "adjust": 0.0, "note": "flows: no supply data", "top10_pct": None}

    # Skip the largest account (the liquidity pool) and measure the next ten.
    top10_ex_pool = sum(sorted(amounts, reverse=True)[1:11])
    pct = top10_ex_pool / supply * 100.0

    if pct >= 50:
        return {
            "ok": True,
            "reject": f"whale concentration: top 10 ex-pool wallets hold {pct:.0f}%",
            "adjust": 0.0,
            "note": f"top 10 ex-pool hold {pct:.0f}%",
            "top10_pct": round(pct, 1),
        }

    if pct >= 30:
        adjust, note = -10.0, f"top 10 ex-pool hold {pct:.0f}% (-10)"
    elif pct <= 10:
        adjust, note = 4.0, f"healthy spread (top 10 ex-pool hold {pct:.0f}%) (+4)"
    else:
        adjust, note = 0.0, f"top 10 ex-pool hold {pct:.0f}%"

    return {"ok": True, "reject": None, "adjust": adjust, "note": note, "top10_pct": round(pct, 1)}
