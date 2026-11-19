/**
 * The full pipeline, ported from main.py and the sub-agents. One runCycle is
 * one cron cycle: instructor plan -> parallel scouts -> filter -> analyst ->
 * risk -> paper trader, with exits managed first every time.
 */

import { tokenSnapshots, searchPairs } from "./dexscreener";
import { applyOutcome, loadLearnState, saveLearnState } from "./learn";
import { makeClient, LlmError } from "./llm";
import { COLLECTORS } from "./scouts";
import { disqualify, ruleScore } from "./strategy";
import * as store from "./store";
import type { Candidate, Config, Decision, Env, Lead, Plan, PortfolioState, Snapshot } from "./types";

const AGENT_SOURCES = ["news", "forums", "feeds"];

const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));

function clampInt(value: unknown, low: number, high: number, fallback: number): number {
  const n = Math.floor(Number(value));
  if (!Number.isFinite(n)) return fallback;
  return Math.max(low, Math.min(high, n));
}

function money(value: number): string {
  return `$${(Number(value) || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

// --- instructor -------------------------------------------------------------

export function rulePlan(config: Config, state: PortfolioState): Plan {
  const scan = config.scan;
  const strategy = config.strategy;
  const risk = config.risk;

  let sources = (scan.sources || []).filter((source) => AGENT_SOURCES.includes(source));
  if (!sources.length) sources = [...AGENT_SOURCES];

  const slots = strategy.max_positions - state.open_positions.length;
  let attempts = config.llm.max_candidates_per_cycle || 3;
  let maxCandidates = scan.max_candidates || 8;
  let posture: Plan["risk_posture"] = "normal";
  const notes: string[] = [];

  const lossToday = state.realized_pnl_today;
  const budget = Math.abs(risk.max_daily_loss_usd);
  if (budget && lossToday <= -budget) {
    posture = "pause";
    attempts = 0;
    notes.push(`daily loss limit spent (${lossToday.toFixed(2)})`);
  } else if (budget && lossToday <= -0.5 * budget) {
    posture = "defensive";
    attempts = Math.max(1, Math.floor(attempts / 2));
    maxCandidates = Math.max(3, Math.floor(maxCandidates / 2));
    notes.push(`half the daily loss budget is gone (${lossToday.toFixed(2)})`);
  }

  if (slots <= 0) {
    attempts = 0;
    notes.push("no free position slots");
  }

  const equity = state.equity_usd;
  if (equity > 0 && state.exposure_usd / equity >= 0.5 && posture === "normal") {
    posture = "defensive";
    notes.push("half the equity is already committed");
  }

  const directives = {
    normal: "Standard posture: hunt broadly, keep only setups that clear the filters, size at plan.",
    defensive: "Defensive posture: fewer entries, stricter reading, no chasing already-pumped setups.",
    pause: "Paused posture: scan and record only, open nothing this cycle.",
  };

  return {
    directive: directives[posture],
    sources,
    max_candidates: maxCandidates,
    max_candidates_per_cycle: Math.max(0, attempts),
    risk_posture: posture,
    notes,
    slots_free: slots,
    source_of_truth: "rules",
  };
}

const INSTRUCTOR_SYSTEM =
  "You are the instructor sub-agent of a Solana meme coin paper-trading desk. " +
  "You command four downstream agents: scouts that hunt leads on news sites and forums, " +
  "a filter that resolves and screens them, an analyst that judges a setup and gives a thesis, " +
  "and a risk manager that refuses anything outside its limits. " +
  "You do not pick tokens yourself and you never see prices; you decide the plan for this cycle. " +
  "Be conservative when the portfolio is losing, over-exposed or out of slots. " +
  'Reply with JSON only: {"directive": "...", "sources": ["news","forums","feeds"], ' +
  '"max_candidates": 1-20, "max_candidates_per_cycle": 0-5, ' +
  '"risk_posture": "normal" or "defensive" or "pause", "notes": ["short strings"]}';

async function instructor(env: Env, config: Config, state: PortfolioState): Promise<Plan> {
  const base = rulePlan(config, state);
  const client = makeClient(env);
  if (!client.available) return base;

  try {
    const raw = await client.chatJson(
      INSTRUCTOR_SYSTEM,
      "Set this cycle's plan. The rules plan is a safe default you may tighten, not loosen.\n" +
        JSON.stringify(
          {
            chain: config.chain,
            available_sources: AGENT_SOURCES,
            knobs: config.strategy,
            portfolio: {
              cash_usd: state.cash_usd,
              equity_usd: state.equity_usd,
              exposure_usd: state.exposure_usd,
              unrealized_pnl_usd: state.unrealized_pnl_usd,
              trades_today: state.trades_today,
              realized_pnl_today: state.realized_pnl_today,
            },
            open_positions: state.open_positions.map((p) => ({ symbol: p.symbol, entry_usd: p.entry_usd })),
            slots_free: base.slots_free,
            rules_plan: base,
          },
          null,
          2
        )
    );

    const sources = (raw.sources || []).filter((s: unknown) => AGENT_SOURCES.includes(String(s)));
    const posture = ["normal", "defensive", "pause"].includes(String(raw.risk_posture)) ? String(raw.risk_posture) : "normal";
    return {
      directive: String(raw.directive || base.directive).slice(0, 400),
      sources: sources.length ? sources : base.sources,
      max_candidates: clampInt(raw.max_candidates, 1, config.scan.max_candidates, base.max_candidates),
      max_candidates_per_cycle: posture === "pause" ? 0 : clampInt(raw.max_candidates_per_cycle, 0, config.llm.max_candidates_per_cycle || 3, base.max_candidates_per_cycle),
      risk_posture: posture as Plan["risk_posture"],
      notes: Array.isArray(raw.notes) ? raw.notes.slice(0, 6).map((n: unknown) => String(n).slice(0, 200)) : [],
      source_of_truth: "llm",
    };
  } catch (exc) {
    const message = exc instanceof LlmError ? exc.message : String(exc);
    console.log(`instructor model call failed, keeping the rules plan: ${message}`);
    base.notes = [...base.notes, `model unavailable: ${message.slice(0, 160)}`];
    return base;
  }
}

// --- filter -----------------------------------------------------------------

async function resolveAndScore(
  env: Env,
  config: Config,
  leads: Lead[],
  weights: Awaited<ReturnType<typeof loadLearnState>>["mult"],
  maxCandidates: number,
  exclude: Set<string>
): Promise<{ candidates: Candidate[]; priced: number; skipped: { symbol: string; reason: string }[] }> {
  const scan = config.scan;
  const merged = new Map<string, { snapshot: Snapshot; corroboration: number; sources: Set<string> }>();

  const tally = (list: Lead[]) => {
    const mints: string[] = [];
    const tickerMentions = new Map<string, number>();
    const tickerLead = new Map<string, Lead>();
    for (const lead of list) {
      for (const mint of lead.mints || []) mints.push(mint);
      for (const ticker of lead.tickers || []) {
        const cleaned = String(ticker).trim().toUpperCase();
        if (cleaned.length < 2) continue;
        tickerMentions.set(cleaned, (tickerMentions.get(cleaned) || 0) + 1);
        if (!tickerLead.has(cleaned)) tickerLead.set(cleaned, lead);
      }
    }
    return { mints: [...new Set(mints)], tickerMentions, tickerLead };
  };

  const entry = (snap: Snapshot, source: string) => {
    const address = snap.token_address;
    if (!address) return;
    const existing = merged.get(address);
    if (!existing) {
      merged.set(address, { snapshot: snap, corroboration: 1, sources: new Set([source]) });
    } else {
      if ((snap.liquidity_usd || 0) > (existing.snapshot.liquidity_usd || 0)) existing.snapshot = snap;
      existing.sources.add(source);
    }
  };

  const { mints, tickerMentions, tickerLead } = tally(leads);
  const budgetMints = mints.slice(0, scan.max_addresses || 30);
  if (budgetMints.length) {
    try {
      const priced = await tokenSnapshots(config.chain, budgetMints);
      for (const snap of Object.values(priced)) entry(snap, "direct");
    } catch (exc) {
      console.log(`direct mint pricing failed: ${exc}`);
    }
  }

  const ignored = new Set((scan.ignore_tickers || []).map((t) => String(t).toUpperCase()));
  let lookups = 0;
  for (const [ticker, mentions] of tickerMentions) {
    if (ignored.has(ticker)) continue;
    if (lookups >= (scan.max_ticker_lookups || 8)) {
      console.log("ticker lookup budget exhausted");
      break;
    }
    lookups += 1;
    try {
      const pairs = await searchPairs(ticker);
      let best: Snapshot | null = null;
      for (const snap of pairs) {
        if (snap.chain !== config.chain) continue;
        if (String(snap.symbol || "").toUpperCase() !== ticker) continue;
        if (!best || (snap.liquidity_usd || 0) > (best.liquidity_usd || 0)) best = snap;
      }
      if (!best) continue;
      entry(best, tickerLead.get(ticker)?.source || "direct");
      const current = merged.get(best.token_address!);
      if (current) current.corroboration += mentions;
    } catch (exc) {
      console.log(`search ${ticker} failed: ${exc}`);
    }
  }

  const candidates: Candidate[] = [];
  const skipped: { symbol: string; reason: string }[] = [];
  for (const [address, item] of merged) {
    if (exclude.has(address)) continue;
    const snap = item.snapshot;
    const reason = disqualify(snap, config);
    if (reason) {
      skipped.push({ symbol: snap.symbol || "?", reason });
      continue;
    }
    const rule = ruleScore(snap, config, weights);
    const corroboration = item.corroboration;
    if (corroboration > 1) {
      const bonus = Math.min(2.0 * (corroboration - 1), 8.0);
      rule.score = Math.round(Math.min(100.0, rule.score + bonus) * 10) / 10;
      rule.signals.push(`mentioned ${corroboration}x across sources (+${bonus.toFixed(0)})`);
    }
    candidates.push({ snapshot: snap, rule, corroboration, sources: [...item.sources].sort() });
  }

  candidates.sort((a, b) => b.rule.score - a.rule.score);
  return { candidates: candidates.slice(0, maxCandidates), priced: merged.size, skipped };
}

// --- analyst ----------------------------------------------------------------

const ANALYST_SYSTEM =
  "You are the analyst sub-agent of a Solana meme coin paper-trading bot. " +
  "You receive one token's live market snapshot and a deterministic rule score. " +
  "Meme coins are reflexive and most go to zero: prefer clean depth, genuine buy pressure, " +
  "and a readable narrative; be wary of one-sided volume, entries already up hundreds of percent " +
  "in 24 hours, and tokens old enough that the story has gone stale. " +
  "Never invent facts that are not in the snapshot. " +
  'Reply with JSON only: {"action":"buy" or "skip","score":0-100,"confidence":0-100,' +
  '"thesis":"one or two sentences","risks":["short strings"]}';

const PROMPT_FIELDS = [
  "symbol", "name", "dex", "price_usd", "liquidity_usd", "fdv", "market_cap",
  "volume_m5", "volume_h1", "volume_h24", "change_m5", "change_h1", "change_h6", "change_h24",
  "buys_h1", "sells_h1", "buys_h24", "sells_h24", "age_minutes", "boosts_active", "socials",
];

async function analyst(env: Env, config: Config, snap: Snapshot, ruleScored: Candidate["rule"]): Promise<Decision> {
  const fallback = (source: "llm" | "rules", decision?: Partial<Decision>): Decision => {
    const action = decision?.action || (ruleScored.score >= config.strategy.buy_score ? "buy" : "skip");
    return {
      action,
      score: clamp(Number(decision?.score ?? ruleScored.score), 0, 100),
      confidence: clamp(Number(decision?.confidence ?? 50), 0, 100),
      thesis: (decision?.thesis || `rule engine: ${ruleScored.signals.slice(0, 3).join("; ")}`).slice(0, 600),
      risks: Array.isArray(decision?.risks) ? decision!.risks!.slice(0, 5).map((r) => String(r).slice(0, 200)) : [],
      source,
      rule_score: ruleScored.score,
      rule_signals: ruleScored.signals,
    };
  };

  const client = makeClient(env);
  if (!client.available) return fallback("rules");

  try {
    const brief = {
      chain: config.chain,
      snapshot: Object.fromEntries(PROMPT_FIELDS.map((key) => [key, (snap as any)[key]])),
      rule_score: ruleScored.score,
      rule_signals: ruleScored.signals,
      buy_threshold: config.strategy.buy_score,
      planned_position_usd: config.strategy.position_usd,
      take_profit_pct: config.strategy.take_profit_pct,
      stop_loss_pct: config.strategy.stop_loss_pct,
    };
    const raw = await client.chatJson(
      ANALYST_SYSTEM,
      "Evaluate this candidate for a short holding period.\n" + JSON.stringify(brief, null, 2)
    );
    const action = String(raw.action || "").trim().toLowerCase();
    return fallback("llm", {
      action: action === "buy" || action === "skip" ? action : undefined,
      score: raw.score,
      confidence: raw.confidence,
      thesis: String(raw.thesis || ""),
      risks: Array.isArray(raw.risks) ? raw.risks : [],
    });
  } catch (exc) {
    const message = exc instanceof LlmError ? exc.message : String(exc);
    const decided = fallback("rules");
    decided.llm_error = message.slice(0, 300);
    return decided;
  }
}

// --- risk -------------------------------------------------------------------

function blocked(reason: string, notes: string[]) {
  return { approved: false as const, reason, size_usd: 0, notes };
}

function riskCheck(
  config: Config,
  snap: Snapshot,
  decision: Decision,
  state: PortfolioState
): { approved: boolean; reason: string; size_usd: number; notes: string[] } {
  const notes: string[] = [];
  const token = snap.token_address;
  if (!token) return blocked("candidate has no token address", notes);
  if (decision.action !== "buy") return blocked(`analyst says ${decision.action || "nothing"}`, notes);

  const score = decision.score;
  const threshold = config.strategy.buy_score;
  if (score < threshold) return blocked(`score ${score.toFixed(0)} below threshold ${threshold.toFixed(0)}`, notes);

  if (state.open_positions.length >= config.strategy.max_positions) {
    return blocked(`position slots full (${state.open_positions.length})`, notes);
  }
  if (state.open_positions.some((p) => p.token_address === token)) return blocked("already holding this token", notes);

  const cash = state.cash_usd;
  const equity = state.equity_usd;
  if (equity <= 0) return blocked("equity is zero", notes);
  if (cash <= 0) return blocked("no paper cash left", notes);

  if (state.realized_pnl_today <= -Math.abs(config.risk.max_daily_loss_usd)) {
    return blocked(`daily loss limit hit ($${state.realized_pnl_today.toFixed(2)})`, notes);
  }
  if (state.trades_today >= config.risk.max_trades_per_day) {
    return blocked(`daily trade cap hit (${state.trades_today})`, notes);
  }

  const lastTouched = state.cooldowns[token];
  if (lastTouched) {
    const elapsed = (store.now() - lastTouched) / 60.0;
    if (elapsed < config.risk.cooldown_minutes) {
      return blocked(`cooldown for this token (${elapsed.toFixed(0)}m of ${config.risk.cooldown_minutes.toFixed(0)}m)`, notes);
    }
  }

  const perPosition = (equity * config.risk.max_position_pct_of_equity) / 100.0;
  let size = Math.min(config.strategy.position_usd, perPosition, cash);
  notes.push(`equity $${equity.toFixed(2)} allows $${perPosition.toFixed(2)} per position`);

  const liquidity = snap.liquidity_usd || 0;
  const liquidityCap = liquidity * config.risk.max_position_pct_of_liquidity;
  if (liquidityCap > 0 && size > liquidityCap) {
    size = liquidityCap;
    notes.push(`capped to ${(config.risk.max_position_pct_of_liquidity * 100).toFixed(1)}% of $${Math.round(liquidity).toLocaleString()} liquidity`);
  }

  const exposureRoom = (equity * config.risk.max_exposure_pct) / 100.0 - state.exposure_usd;
  if (size > exposureRoom) {
    size = Math.max(exposureRoom, 0.0);
    notes.push(`capped by total exposure ceiling ($${exposureRoom.toFixed(2)} room)`);
  }

  const minimum = config.strategy.min_position_usd || 5;
  if (size < minimum) return blocked(`sized $${size.toFixed(2)}, below the $${minimum.toFixed(2)} minimum`, notes);

  return { approved: true, reason: `approved $${size.toFixed(2)} at score ${score.toFixed(0)}`, size_usd: Math.round(size * 100) / 100, notes };
}

// --- cycle ------------------------------------------------------------------

export interface CycleResult {
  opened: unknown[];
  closed: unknown[];
  plan: Plan;
}

async function manageExits(env: Env, config: Config): Promise<unknown[]> {
  const positions = await store.openPositions(env);
  if (!positions.length) return [];

  const state = await store.portfolioState(env, config, await store.getState(env));
  const exits: unknown[] = [];

  for (const position of positions) {
    const snap = state.marks[position.token_address];
    if (!snap?.price_usd) {
      console.log(`${position.symbol}: no fresh price, holding`);
      continue;
    }

    const price = Number(snap.price_usd);
    const peak = Math.max(Number(position.peak_price || 0), price);
    if (peak > Number(position.peak_price || 0)) {
      await env.DB.prepare("UPDATE positions SET peak_price = ? WHERE id = ?").bind(peak, position.id).run();
    }

    const entry = Number(position.entry_price) || price;
    const changePct = (price / entry - 1.0) * 100.0;
    const peakChangePct = (peak / entry - 1.0) * 100.0;
    const heldMinutes = (store.now() - Number(position.opened_at)) / 60.0;
    const rules = config.strategy;

    let reason: string | null = null;
    if (changePct <= rules.stop_loss_pct) {
      reason = `stop loss ${changePct >= 0 ? "+" : ""}${changePct.toFixed(0)}%`;
    } else if (changePct >= rules.take_profit_pct) {
      reason = `take profit +${changePct.toFixed(0)}%`;
    } else {
      const trailing = rules.trailing_stop_pct || 0;
      const drawdown = peak > 0 ? ((peak - price) / peak) * 100.0 : 0;
      if (trailing > 0 && peakChangePct > trailing && drawdown >= trailing) {
        reason = `trailing stop ${drawdown.toFixed(0)}% off a +${peakChangePct.toFixed(0)}% peak`;
      } else if (heldMinutes >= rules.max_hold_minutes) {
        reason = `time exit after ${heldMinutes.toFixed(0)}m`;
      }
    }

    if (!reason) continue;

    try {
      const current = await store.getState(env);
      const result = await store.brokerSell(env, current, position, snap, config, reason);
      // Self-learning: the entry-time feature contributions decide which signal
      // groups get credit or blame for this outcome.
      let features: Record<string, number> = {};
      try {
        features = position.features ? JSON.parse(position.features) : {};
      } catch {
        features = {};
      }
      const learn = await loadLearnState(env);
      applyOutcome(learn, features, result.pnl_pct);
      await saveLearnState(env, learn);

      console.log(`CLOSED ${position.symbol} - ${reason}: ${money(result.pnl_usd)} (${result.pnl_pct >= 0 ? "+" : ""}${result.pnl_pct.toFixed(1)}%)`);
      exits.push({ symbol: position.symbol, ...result, reason });
    } catch (exc) {
      console.log(`${position.symbol}: close failed: ${exc}`);
    }
  }
  return exits;
}

export async function runCycle(env: Env, config: Config): Promise<CycleResult> {
  const day = store.today();
  await store.bootstrap(env, config.risk.starting_cash_usd, day);
  const stateRow = await store.rolloverDay(env, day);
  const killed = stateRow.kill === "1";

  const closed = await manageExits(env, config);

  const opened: unknown[] = [];
  let plan: Plan = { directive: "", sources: [], max_candidates: 0, max_candidates_per_cycle: 0, risk_posture: "normal", notes: [], source_of_truth: "rules" };
  let leadsIn = 0;
  let priced = 0;
  let candidateCount = 0;

  if (!killed) {
    const fresh = await store.getState(env);
    const state = await store.portfolioState(env, config, fresh);
    plan = await instructor(env, config, state);

    const exclude = new Set(state.open_positions.map((p) => p.token_address));
    const learn = await loadLearnState(env);
    const collectors = plan.sources.map((source) => COLLECTORS[source]).filter(Boolean);

    const leadGroups = await Promise.all(
      collectors.map(async (collect) => {
        try {
          return await collect(config);
        } catch (exc) {
          console.log(`scout failed: ${exc}`);
          return [] as Lead[];
        }
      })
    );
    const leads = leadGroups.flat();
    leadsIn = leads.length;

    try {
      const filtered = await resolveAndScore(env, config, leads, learn.mult, plan.max_candidates, exclude);
      priced = filtered.priced;
      const candidates = filtered.candidates;
      candidateCount = candidates.length;

      // Rewrite the dashboard's token table with this cycle's scan.
      const scanTs = store.now();
      await env.DB.prepare("DELETE FROM candidates").run();
      if (candidates.length) {
        await env.DB.batch(
          candidates.map((candidate) =>
            env.DB.prepare(
              "INSERT INTO candidates (ts, token_address, symbol, score, corroboration, sources, snapshot, rule, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'scanned')"
            ).bind(
              scanTs,
              candidate.snapshot.token_address || "",
              candidate.snapshot.symbol || "?",
              candidate.rule.score,
              candidate.corroboration,
              candidate.sources.join(","),
              JSON.stringify(candidate.snapshot),
              JSON.stringify(candidate.rule)
            )
          )
        );
      }

      // Try entries, re-reading state between attempts like main.py does.
      let slots = plan.slots_free ?? config.strategy.max_positions - state.open_positions.length;
      const attempts = plan.max_candidates_per_cycle;
      if (attempts > 0) {
        for (const candidate of candidates.slice(0, attempts)) {
          if (slots <= 0) break;
          const snap = candidate.snapshot;
          const symbol = snap.symbol || "?";

          const decision = await analyst(env, config, snap, candidate.rule);
          await store.recordDecision(env, "analyst", decision, snap.token_address, symbol);
          console.log(`${symbol}: ${decision.action} score ${decision.score.toFixed(0)} via ${decision.source} - ${decision.thesis.slice(0, 120)}`);

          const freshState = await store.getState(env);
          const portfolio = await store.portfolioState(env, config, freshState);
          const verdict = riskCheck(config, snap, decision, portfolio);
          await store.recordDecision(env, "risk", verdict, snap.token_address, symbol);
          if (!verdict.approved) {
            console.log(`${symbol}: blocked - ${verdict.reason}`);
            if (snap.token_address) {
              await env.DB.prepare("UPDATE candidates SET status = 'blocked' WHERE token_address = ? AND ts = ?")
                .bind(snap.token_address, scanTs).run();
            }
            continue;
          }

          try {
            const result = await store.brokerBuy(env, freshState, snap, verdict.size_usd, config, decision.thesis);
            // Save the raw entry-time contributions so the learner can grade them later.
            await env.DB.prepare("UPDATE positions SET features = ? WHERE token_address = ? AND status = 'open'")
              .bind(JSON.stringify(candidate.rule.groups), snap.token_address || "")
              .run();
            if (snap.token_address) {
              await env.DB.prepare("UPDATE candidates SET status = 'entered' WHERE token_address = ? AND ts = ?")
                .bind(snap.token_address, scanTs).run();
            }
            console.log(`OPENED ${symbol} ${money(verdict.size_usd)} @ ${result.fill_price}`);
            opened.push({ symbol, size_usd: verdict.size_usd, ...result });
            slots -= 1;
          } catch (exc) {
            console.log(`${symbol}: open failed: ${exc}`);
          }
        }
      }
    } catch (exc) {
      console.log(`filter failed: ${exc}`);
    }
  } else {
    console.log("kill switch active: exits only, no new entries");
  }

  const finalState = await store.getState(env);
  const portfolio = await store.portfolioState(env, config, finalState);

  await env.DB.prepare(
    "INSERT INTO cycles (ts, plan, leads_in, priced, candidates, opened, closed, equity_usd, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
  )
    .bind(
      store.now(),
      JSON.stringify(plan),
      leadsIn,
      priced,
      candidateCount,
      opened.length,
      closed.length,
      portfolio.equity_usd,
      killed ? "kill switch active" : ""
    )
    .run();

  console.log(`equity ${money(portfolio.equity_usd)} (cash ${money(portfolio.cash_usd)}, unrealized ${money(portfolio.unrealized_pnl_usd)})`);
  return { opened, closed, plan };
}
