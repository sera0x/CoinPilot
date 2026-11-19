/** Entrypoint: a cron-triggered trading cycle plus the dashboard's JSON API. */

import { loadConfig } from "./config";
import { runCycle } from "./engine";
import { loadLearnState } from "./learn";
import * as store from "./store";
import type { Env, PositionRow } from "./types";

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

function authorized(request: Request, env: Env): boolean {
  if (!env.DASHBOARD_TOKEN) return true; // no token set: dashboard is open (paper only)
  const url = new URL(request.url);
  const supplied = url.searchParams.get("token") || request.headers.get("Authorization") || "";
  return supplied === env.DASHBOARD_TOKEN || `Bearer ${env.DASHBOARD_TOKEN}` === supplied;
}

async function summary(env: Env): Promise<Response> {
  const config = loadConfig(env);
  const state = await store.getState(env);
  const portfolio = await store.portfolioState(env, config, state);

  const { results: positions } = await env.DB.prepare(
    "SELECT * FROM positions WHERE status = 'open' ORDER BY opened_at"
  ).all<PositionRow>();
  const { results: trades } = await env.DB.prepare(
    "SELECT * FROM trades ORDER BY ts DESC LIMIT 40"
  ).all();
  const { results: candidates } = await env.DB.prepare(
    "SELECT ts, token_address, symbol, score, corroboration, sources, snapshot, rule, status FROM candidates ORDER BY score DESC"
  ).all();
  const { results: cycles } = await env.DB.prepare(
    "SELECT * FROM cycles ORDER BY ts DESC LIMIT 8"
  ).all();
  const learn = await loadLearnState(env);

  const positionsView = (positions || []).map((p) => {
    const price = p.mark_price || p.entry_price;
    const value = p.qty * price;
    const pnl = value - p.entry_usd;
    const pnlPct = p.entry_usd ? (pnl / p.entry_usd) * 100 : 0;
    return {
      id: p.id,
      symbol: p.symbol,
      token_address: p.token_address,
      entry_usd: p.entry_usd,
      entry_price: p.entry_price,
      mark_price: price,
      value_usd: value,
      pnl_usd: pnl,
      pnl_pct: pnlPct,
      held_minutes: (store.now() - p.opened_at) / 60.0,
      peak_price: p.peak_price,
      thesis: p.thesis,
    };
  });

  return json({
    mode: "paper",
    chain: config.chain,
    account: {
      starting_cash_usd: Number(state.starting_cash_usd || 0),
      cash_usd: portfolio.cash_usd,
      equity_usd: portfolio.equity_usd,
      unrealized_pnl_usd: portfolio.unrealized_pnl_usd,
      realized_pnl_today: Number(state.realized_pnl_today || 0),
      realized_pnl_total: Number(state.realized_pnl_total || 0),
      trades_today: portfolio.trades_today,
      day: state.day,
      kill: state.kill === "1",
    },
    positions: positionsView,
    trades: trades || [],
    candidates: candidates || [],
    cycles: cycles || [],
    learning: learn,
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/api/summary") {
      try {
        return await summary(env);
      } catch (exc) {
        return json({ error: String(exc) }, 500);
      }
    }

    if (url.pathname === "/api/kill" && request.method === "POST") {
      if (!authorized(request, env)) return json({ error: "unauthorized" }, 401);
      const body = await request.json().catch(() => ({}));
      await store.setState(env, { kill: body && (body as any).kill ? "1" : "0" });
      return json({ ok: true, kill: Boolean(body && (body as any).kill) });
    }

    if (url.pathname === "/api/cycle" && request.method === "POST") {
      if (!authorized(request, env)) return json({ error: "unauthorized" }, 401);
      try {
        const result = await runCycle(env, loadConfig(env));
        return json({ ok: true, ...result });
      } catch (exc) {
        return json({ error: String(exc) }, 500);
      }
    }

    // Everything else is the dashboard's static files.
    return env.ASSETS.fetch(request);
  },

  async scheduled(_event: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    ctx.waitUntil(
      runCycle(env, loadConfig(env)).catch((exc) => {
        console.error(`cycle failed: ${exc}`);
      })
    );
  },
};
