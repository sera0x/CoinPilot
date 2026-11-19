"""Sub-agent: resolve raw leads into prices, then keep only what survives the filters."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coinpilot import agentio, market, strategy
from coinpilot import config as config_mod

SOURCE_KIND = ("news", "forums", "feeds")


def _entry(merged: dict, snap: dict) -> dict | None:
    address = snap.get("token_address")
    if not address:
        return None
    entry = merged.get(address)
    if entry is None:
        entry = {"snapshot": snap, "corroboration": 1, "sources": set()}
        merged[address] = entry
    elif (snap.get("liquidity_usd") or 0) > (entry["snapshot"].get("liquidity_usd") or 0):
        entry["snapshot"] = snap
    return entry


def _tally(leads: list[dict]) -> tuple[list[str], dict[str, int], dict[str, dict]]:
    """Split leads into direct mint addresses and tickers, counting mentions."""

    mints: list[str] = []
    ticker_mentions: dict[str, int] = {}
    ticker_lead: dict[str, dict] = {}
    for lead in leads:
        for mint in lead.get("mints") or []:
            mints.append(mint)
        for ticker in lead.get("tickers") or []:
            cleaned = str(ticker).strip().upper()
            if len(cleaned) < 2:
                continue
            ticker_mentions[cleaned] = ticker_mentions.get(cleaned, 0) + 1
            ticker_lead.setdefault(cleaned, lead)
    return list(dict.fromkeys(mints)), ticker_mentions, ticker_lead


def _resolve_offline(payload: dict, merged: dict, ticker_mentions: dict[str, int]) -> None:
    for snap in payload.get("snapshots") or []:
        entry = _entry(merged, snap)
        if entry is not None:
            entry["sources"].add("offline")
    for lead in payload.get("leads") or []:
        for ticker in lead.get("tickers") or []:
            cleaned = str(ticker).strip().upper()
            for entry in merged.values():
                if str(entry["snapshot"].get("symbol") or "").upper() == cleaned:
                    entry["corroboration"] += 1
                    entry["sources"].add(lead.get("source") or "offline")
        for mint in lead.get("mints") or []:
            entry = merged.get(mint)
            if entry is not None:
                entry["corroboration"] += 1
                entry["sources"].add(lead.get("source") or "offline")
    agentio.log(f"offline mode: {len(merged)} pre-priced token(s)")


def _resolve_live(config: dict, leads: list[dict], merged: dict, ticker_mentions: dict[str, int], ticker_lead: dict[str, dict]) -> None:
    chain = config["chain"]
    scan = config["scan"]
    mints, _, _ = _tally(leads)

    budget = int(scan.get("max_addresses") or 60)
    mints = mints[:budget]
    if mints:
        try:
            for snap in market.token_snapshots(chain, mints).values():
                entry = _entry(merged, snap)
                if entry is not None:
                    entry["sources"].add("direct")
        except Exception as exc:  # noqa: BLE001
            agentio.log(f"direct mint pricing failed: {exc}")
        agentio.log(f"priced {len(mints)} direct mint(s)")

    ignored = {str(ticker).upper() for ticker in (scan.get("ignore_tickers") or [])}
    lookups = 0
    for ticker in ticker_mentions:
        if ticker in ignored:
            continue
        if lookups >= int(scan.get("max_ticker_lookups") or 25):
            agentio.log("ticker lookup budget exhausted")
            break
        lookups += 1
        try:
            pairs = market.search_pairs(ticker)
        except Exception as exc:  # noqa: BLE001
            agentio.log(f"search {ticker} failed: {exc}")
            continue

        best = None
        for snap in pairs:
            if snap.get("chain") != chain:
                continue
            if str(snap.get("symbol") or "").upper() != ticker:
                continue
            if best is None or (snap.get("liquidity_usd") or 0) > (best.get("liquidity_usd") or 0):
                best = snap
        if best is None:
            continue

        entry = _entry(merged, best)
        if entry is not None:
            entry["corroboration"] += ticker_mentions.get(ticker, 1)
            source = (ticker_lead.get(ticker) or {}).get("source")
            if source in SOURCE_KIND:
                entry["sources"].add(source)

    agentio.log(f"resolved {lookups} ticker(s) into {len(merged)} token(s)")


def handler(payload: dict) -> dict:
    config = config_mod.load_config()
    scan = config["scan"]
    leads = payload.get("leads") or []
    exclude = {token for token in (payload.get("exclude_tokens") or []) if token}

    merged: dict[str, dict] = {}
    mints, ticker_mentions, ticker_lead = _tally(leads)

    if payload.get("offline"):
        _resolve_offline(payload, merged, ticker_mentions)
    else:
        _resolve_live(config, leads, merged, ticker_mentions, ticker_lead)

    limit = int(payload.get("max_candidates") or scan.get("max_candidates") or 8)
    candidates: list[dict] = []
    skipped: list[dict] = []

    for address, entry in merged.items():
        if address in exclude:
            continue
        snap = entry["snapshot"]
        reason = strategy.disqualify(snap, config)
        if reason:
            skipped.append({"symbol": snap.get("symbol"), "reason": reason})
            continue

        rule = strategy.rule_score(snap, config)
        corroboration = int(entry["corroboration"])
        if corroboration > 1:
            bonus = min(2.0 * (corroboration - 1), 8.0)
            rule["score"] = round(min(100.0, rule["score"] + bonus), 1)
            rule["signals"].append(f"mentioned {corroboration}x across sources (+{bonus:.0f})")

        candidates.append(
            {
                "snapshot": snap,
                "rule": rule,
                "corroboration": corroboration,
                "sources": sorted(entry["sources"]),
            }
        )

    candidates.sort(key=lambda item: item["rule"]["score"], reverse=True)

    return {
        "ok": True,
        "candidates": candidates[:limit],
        "priced": len(merged),
        "leads_in": len(leads),
        "skipped_count": len(skipped),
        "skipped_sample": skipped[:10],
    }


if __name__ == "__main__":
    agentio.serve(handler)
