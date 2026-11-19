/** D1 persistence for the paper portfolio, plus the simulated fills (paper broker). */

import { tokenSnapshots } from "./dexscreener";
import type { Config, PortfolioState, PositionRow, Snapshot } from "./types";

export function now(): number {
  return Date.now() / 1000.0;
}

export function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export async function getState(env: Env): Promise<Record<string, string>> {
  const { results } = await env.DB.prepare("SELECT key, value FROM state").all<{ key: string; value: string }>();
  const out: Record<string, string> = {};
  for (const row of results) out[row.key] = row.value;
  return out;
}

export async function setState(env: Env, values: Record<string, string | number>): Promise<void> {
  const statements = Object.entries(values).map(([key, value]) =>
    env.DB.prepare(
      "INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value"
    ).bind(key, String(value))
  );
  if (statements.length) await env.DB.batch(statements);
}

export async function bootstrap(env: Env, startingCash: number, day: string): Promise<Record<string, string>> {
  let state = await getState(env);
  if (!state.cash_usd) {
    await setState(env, {
      starting_cash_usd: startingCash.toFixed(6),
      cash_usd: startingCash.toFixed(6),
      day,
      realized_pnl_today: "0",
      realized_pnl_total: "0",
      trades_today: "0",
      kill: "0",
      created_at: String(Math.floor(now())),
    });
    state = await getState(env);
  }
  return state;
}

export async function rolloverDay(env: Env, day: string): Promise<Record<string, string>> {
  const state = await getState(env);
  if (state.day !== day) {
    await setState(env, { day, realized_pnl_today: "0", trades_today: "0" });
    return getState(env);
  }
  return state;
}

export async function openPositions(env: Env): Promise<PositionRow[]> {
  const { results } = await env.DB.prepare(
    "SELECT * FROM positions WHERE status = 'open' ORDER BY opened_at"
  ).all<PositionRow>();
  return results || [];
}

export async function recentTokenActivity(env: Env, sinceTs: number): Promise<Record<string, number>> {
  const { results } = await env.DB.prepare(
    "SELECT token_address, MAX(ts) AS last_ts FROM trades WHERE ts >= ? GROUP BY token_address"
  )
    .bind(sinceTs)
    .all<{ token_address: string; last_ts: number }>();
  const out: Record<string, number> = {};
  for (const row of results ?? []) out[row.token_address] = Number(row.last_ts);
  return out;
}

/** Portfolio state for the risk agent, with live marks for every open position. */
export async function portfolioState(env: Env, config: Config, state: Record<string, string>): Promise<PortfolioState> {
  const positions = await openPositions(env);
  const marks: Record<string, Snapshot> = {};
  if (positions.length) {
    try {
      Object.assign(marks, await tokenSnapshots(config.chain, positions.map((p) => p.token_address)));
    } catch (exc) {
      console.log(`mark lookup failed: ${exc}`);
    }
  }

  const cash = Number(state.cash_usd || 0);
  let exposure = 0;
  let marketValue = 0;
  for (const position of positions) {
    const mark = marks[position.token_address];
    const price = mark?.price_usd || position.entry_price;
    exposure += position.entry_usd;
    marketValue += position.qty * price;
    if (mark?.price_usd) {
      await env.DB.prepare("UPDATE positions SET mark_price = ?, mark_ts = ? WHERE id = ?")
        .bind(mark.price_usd, now(), position.id)
        .run();
    }
  }

  const cooldownMinutes = config.risk.cooldown_minutes;
  const cooldowns = await recentTokenActivity(env, now() - cooldownMinutes * 60);

  return {
    cash_usd: cash,
    equity_usd: cash + marketValue,
    exposure_usd: exposure,
    unrealized_pnl_usd: marketValue - exposure,
    open_positions: positions.map((p) => ({ id: p.id, token_address: p.token_address, symbol: p.symbol, entry_usd: p.entry_usd })),
    trades_today: Math.floor(Number(state.trades_today || 0)),
    realized_pnl_today: Number(state.realized_pnl_today || 0),
    cooldowns,
    marks,
    now: now(),
  };
}

// --- paper broker -----------------------------------------------------------

async function addTrade(
  env: Env,
  row: {
    token_address: string;
    symbol: string;
    side: string;
    qty: number;
    price: number;
    usd: number;
    fee_usd: number;
    reason: string;
    pnl_usd?: number | null;
  }
): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO trades (ts, token_address, symbol, side, qty, price, usd, fee_usd, reason, pnl_usd) " +
      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
  )
    .bind(now(), row.token_address, row.symbol, row.side, row.qty, row.price, row.usd, row.fee_usd, row.reason, row.pnl_usd ?? null)
    .run();
}

async function bumpState(env: Env, patch: (current: Record<string, string>) => Record<string, string | number>): Promise<void> {
  await setState(env, patch(await getState(env)));
}

/** Simulated buy: fees, slippage, cash check. No wallet, no keys. */
export async function brokerBuy(
  env: Env,
  state: Record<string, string>,
  snap: Snapshot,
  sizeUsd: number,
  config: Config,
  thesis: string
): Promise<{ fill_price: number; fee_usd: number; qty: number }> {
  const price = Number(snap.price_usd || 0);
  if (price <= 0) throw new Error("no price for token");
  sizeUsd = Math.round(sizeUsd * 100) / 100;
  if (sizeUsd <= 0) throw new Error("order size is zero");

  const available = Number(state.cash_usd || 0);
  if (sizeUsd > available + 1e-9) throw new Error(`insufficient paper cash: have $${available.toFixed(2)}, need $${sizeUsd.toFixed(2)}`);

  const slip = config.paper.slippage_pct / 100.0;
  const fillPrice = price * (1 + slip);
  const feeUsd = sizeUsd * (config.paper.fee_pct / 100.0);
  const qty = (sizeUsd - feeUsd) / fillPrice;
  if (qty <= 0) throw new Error("size too small after fees");

  const symbol = snap.symbol || "?";
  const result = await env.DB.prepare(
    "INSERT INTO positions (token_address, symbol, pair_address, qty, entry_price, entry_usd, peak_price, opened_at, status, thesis) " +
      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)"
  )
    .bind(snap.token_address, symbol, snap.pair_address || null, qty, fillPrice, sizeUsd, fillPrice, now(), thesis.slice(0, 400))
    .run();

  await addTrade(env, { token_address: snap.token_address!, symbol, side: "buy", qty, price: fillPrice, usd: sizeUsd, fee_usd: feeUsd, reason: thesis });
  await bumpState(env, (current) => ({
    cash_usd: (Number(current.cash_usd || 0) - sizeUsd).toFixed(6),
    trades_today: Math.floor(Number(current.trades_today || 0)) + 1,
  }));
  void result;
  return { fill_price: fillPrice, fee_usd: feeUsd, qty };
}

export interface CloseResult {
  pnl_usd: number;
  pnl_pct: number;
  proceeds: number;
  fill_price: number;
  fee_usd: number;
}

/** Simulated sell: fees, slippage, realized pnl, day rollovers. */
export async function brokerSell(
  env: Env,
  state: Record<string, string>,
  position: PositionRow,
  snap: Snapshot,
  config: Config,
  reason: string
): Promise<CloseResult> {
  const price = Number(snap.price_usd || 0);
  if (price <= 0) throw new Error("no price available");

  const slip = config.paper.slippage_pct / 100.0;
  const fillPrice = price * (1 - slip);
  const gross = position.qty * fillPrice;
  const feeUsd = gross * (config.paper.fee_pct / 100.0);
  const proceeds = gross - feeUsd;
  const pnl = proceeds - position.entry_usd;
  const pnlPct = position.entry_usd ? (pnl / position.entry_usd) * 100.0 : 0;

  await env.DB.prepare(
    "UPDATE positions SET status = 'closed', closed_at = ?, close_price = ?, close_usd = ?, pnl_usd = ? WHERE id = ? AND status = 'open'"
  )
    .bind(now(), fillPrice, proceeds, pnl, position.id)
    .run();

  await addTrade(env, {
    token_address: position.token_address,
    symbol: position.symbol,
    side: "sell",
    qty: position.qty,
    price: fillPrice,
    usd: proceeds,
    fee_usd: feeUsd,
    reason,
    pnl_usd: pnl,
  });

  await bumpState(env, (current) => ({
    cash_usd: (Number(current.cash_usd || 0) + proceeds).toFixed(6),
    realized_pnl_today: (Number(current.realized_pnl_today || 0) + pnl).toFixed(6),
    realized_pnl_total: (Number(current.realized_pnl_total || 0) + pnl).toFixed(6),
    trades_today: Math.floor(Number(current.trades_today || 0)) + 1,
  }));

  return { pnl_usd: pnl, pnl_pct: pnlPct, proceeds, fill_price: fillPrice, fee_usd: feeUsd };
}

export async function recordDecision(
  env: Env,
  stage: string,
  payload: unknown,
  tokenAddress?: string,
  symbol?: string
): Promise<void> {
  await env.DB.prepare("INSERT INTO decisions (ts, token_address, symbol, stage, payload) VALUES (?, ?, ?, ?, ?)")
    .bind(now(), tokenAddress || null, symbol || null, stage, JSON.stringify(payload).slice(0, 8000))
    .run();
}
