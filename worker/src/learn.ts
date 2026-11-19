/**
 * Self-learning: each closed trade nudges the strategy's signal-group weights.
 *
 * Every entry stores the raw per-group score contributions it had at buy time.
 * When the trade closes, groups whose signal aligned with the outcome get a
 * little more influence, groups whose signal contradicted it a little less,
 * each multiplier clamped to [0.5, 2.0] of its base value so a few lucky or
 * unlucky trades can never rewrite the strategy.
 */

import { GROUPS } from "./strategy";
import type { Env, GroupStat, LearnState, SignalGroup } from "./types";

const LEARNING_RATE = 0.02; // weight step per group per trade at +-50% pnl
const MIN_MULT = 0.5;
const MAX_MULT = 2.0;

const EMPTY_STATE: LearnState = {
  mult: GROUPS.reduce((acc, g) => ({ ...acc, [g]: 1.0 }), {} as LearnState["mult"]),
  stats: GROUPS.reduce((acc, g) => ({ ...acc, [g]: { pos_n: 0, pos_sum: 0, neg_n: 0, neg_sum: 0 } }), {} as LearnState["stats"]),
  n: 0,
};

export async function loadLearnState(env: Env): Promise<LearnState> {
  const row = await env.DB.prepare("SELECT value FROM state WHERE key = 'learn_state'").first<{ value: string }>();
  if (!row) return structuredClone(EMPTY_STATE);
  try {
    const parsed = JSON.parse(row.value) as LearnState;
    if (!parsed || typeof parsed !== "object" || !parsed.mult || !parsed.stats) return structuredClone(EMPTY_STATE);
    for (const group of GROUPS) {
      if (!Number.isFinite(parsed.mult[group])) parsed.mult[group] = 1.0;
      if (!parsed.stats[group]) parsed.stats[group] = { pos_n: 0, pos_sum: 0, neg_n: 0, neg_sum: 0 };
    }
    return parsed;
  } catch {
    return structuredClone(EMPTY_STATE);
  }
}

export async function saveLearnState(env: Env, state: LearnState): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO state (key, value) VALUES ('learn_state', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value"
  )
    .bind(JSON.stringify(state))
    .run();
}

/** Update the learned weights from one closed trade. */
export function applyOutcome(state: LearnState, features: Record<string, number>, pnlPct: number): void {
  const pnl = Math.max(-50, Math.min(50, pnlPct || 0));
  state.n += 1;
  for (const group of GROUPS) {
    const contribution = features[group] || 0;
    if (Math.abs(contribution) < 0.01) continue;

    const stat: GroupStat = state.stats[group];
    if (contribution > 0) {
      stat.pos_n += 1;
      stat.pos_sum += pnl;
    } else {
      stat.neg_n += 1;
      stat.neg_sum += pnl;
    }

    // Signal said "yes" (contribution > 0) and the trade won -> trust it more.
    const step = LEARNING_RATE * (pnl / 10) * Math.sign(contribution);
    state.mult[group] = Math.max(MIN_MULT, Math.min(MAX_MULT, state.mult[group] + step));
  }
}
