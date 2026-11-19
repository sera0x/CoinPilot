/** Market data from the public DexScreener API (no key required). */

import type { Snapshot } from "./types";

const BASE = "https://api.dexscreener.com";

export async function fetchJson(url: string, init?: RequestInit): Promise<any> {
  const response = await fetch(url, init);
  if (!response.ok) throw new Error(`HTTP ${response.status} from ${url}`);
  return response.json();
}

function num(value: any): number | null {
  if (value === null || value === undefined) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function pairs(payload: any): any[] {
  if (!payload) return [];
  if (Array.isArray(payload)) return payload.filter((item) => item && typeof item === "object");
  if (typeof payload === "object") {
    if (Array.isArray(payload.pairs)) return payload.pairs;
    return [payload];
  }
  return [];
}

function windowed(values: any, key: string): number | null {
  if (!values || typeof values !== "object") return null;
  return num(values[key]);
}

/** Flatten one DexScreener pair into the fields the strategy cares about. */
export function snapshot(pair: any): Snapshot {
  const base = pair.baseToken || {};
  const quote = pair.quoteToken || {};
  const liquidity = pair.liquidity || {};
  const txns = pair.txns || {};
  const volume = pair.volume || {};
  const change = pair.priceChange || {};
  const info = pair.info || {};
  const boosts = pair.boosts || {};

  const createdAt = num(pair.pairCreatedAt);
  const ageMinutes = createdAt ? Math.max(0, (Date.now() - createdAt) / 60000.0) : null;

  const tx = (window: string, side: string): number | null => {
    const slot = txns && typeof txns === "object" ? txns[window] : null;
    if (!slot || typeof slot !== "object") return null;
    return num(slot[side]);
  };

  return {
    chain: pair.chainId,
    dex: pair.dexId,
    token_address: base.address,
    symbol: base.symbol,
    name: base.name,
    quote_symbol: quote.symbol,
    pair_address: pair.pairAddress,
    price_usd: num(pair.priceUsd),
    liquidity_usd: num(liquidity.usd),
    fdv: num(pair.fdv),
    market_cap: num(pair.marketCap),
    volume_m5: windowed(volume, "m5"),
    volume_h1: windowed(volume, "h1"),
    volume_h24: windowed(volume, "h24"),
    change_m5: windowed(change, "m5"),
    change_h1: windowed(change, "h1"),
    change_h6: windowed(change, "h6"),
    change_h24: windowed(change, "h24"),
    buys_h1: tx("h1", "buys"),
    sells_h1: tx("h1", "sells"),
    buys_h24: tx("h24", "buys"),
    sells_h24: tx("h24", "sells"),
    pair_created_at: createdAt,
    age_minutes: ageMinutes,
    boosts_active: num(boosts.active),
    socials: Array.isArray(info.socials)
      ? info.socials.filter((s: any) => s && typeof s === "object").map((s: any) => String(s.platform || ""))
      : [],
    url: pair.url,
  };
}

/** Deepest pair per token address, fetched in batches of 30. */
export async function tokenSnapshots(chain: string, addresses: string[]): Promise<Record<string, Snapshot>> {
  const unique = [...new Set(addresses.filter(Boolean))];
  const best: Record<string, Snapshot> = {};
  for (let i = 0; i < unique.length; i += 30) {
    const chunk = unique.slice(i, i + 30);
    const url = `${BASE}/tokens/v1/${chain}/${chunk.join(",")}`;
    for (const pair of pairs(await fetchJson(url))) {
      const snap = snapshot(pair);
      const token = snap.token_address;
      if (!token) continue;
      const current = best[token];
      if (!current || (snap.liquidity_usd || 0) > (current.liquidity_usd || 0)) best[token] = snap;
    }
  }
  return best;
}

export async function searchPairs(query: string): Promise<Snapshot[]> {
  const payload = await fetchJson(`${BASE}/latest/dex/search?q=${encodeURIComponent(query)}`);
  return pairs(payload).map(snapshot);
}

async function plainList(path: string): Promise<any[]> {
  const payload = await fetchJson(`${BASE}${path}`);
  if (Array.isArray(payload)) return payload.filter((item) => item && typeof item === "object");
  return [];
}

export function latestProfiles(): Promise<any[]> {
  return plainList("/token-profiles/latest/v1");
}

export function latestBoosts(): Promise<any[]> {
  return plainList("/token-boosts/latest/v1");
}
