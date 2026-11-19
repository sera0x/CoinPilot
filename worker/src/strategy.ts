/** Deterministic rule scoring, ported from coinpilot/strategy.py, with learned weights. */

import type { Config, RuleScore, SignalGroup, Snapshot, Weights } from "./types";

export const GROUPS: SignalGroup[] = [
  "depth",
  "turnover",
  "pressure",
  "momentum1h",
  "momentum6h",
  "mania",
  "age",
  "extras",
];

const BASE_WEIGHTS: Weights = GROUPS.reduce((acc, group) => {
  acc[group] = 1.0;
  return acc;
}, {} as Weights);

function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(high, value));
}

/** Reason to skip a token entirely, or null when it passes the basic filters. */
export function disqualify(snap: Snapshot, config: Config): string | null {
  const scan = config.scan;
  const price = snap.price_usd;
  if (!price || price <= 0) return "no price";

  const liquidity = snap.liquidity_usd || 0;
  if (liquidity < scan.min_liquidity_usd) {
    return `liquidity $${Math.round(liquidity).toLocaleString()} under $${scan.min_liquidity_usd.toLocaleString()}`;
  }

  const volume = snap.volume_h24 || 0;
  if (volume < scan.min_volume_24h_usd) {
    return `24h volume $${Math.round(volume).toLocaleString()} under $${scan.min_volume_24h_usd.toLocaleString()}`;
  }

  const trades = (snap.buys_h24 || 0) + (snap.sells_h24 || 0);
  if (trades < scan.min_trades_24h) return `only ${trades.toFixed(0)} trades in 24h`;

  const age = snap.age_minutes;
  if (age !== null && age !== undefined) {
    if (age < scan.min_age_minutes) return `too new (${age.toFixed(0)}m old)`;
    if (age > scan.max_age_hours * 60) return `too old (${(age / 60).toFixed(0)}h)`;
  }
  return null;
}

/**
 * Score a snapshot 0-100. Each signal group's contribution is scaled by its
 * learned multiplier before being added; base 45 is constant.
 */
export function ruleScore(snap: Snapshot, config: Config, weights: Weights): RuleScore {
  const groups: Partial<Record<SignalGroup, number>> = {};
  const signals: string[] = [];
  // Record the raw contribution; the learned multiplier is applied once, at the sum below.
  const apply = (group: SignalGroup, raw: number, label: string) => {
    if (Math.abs(raw) < 0.01) return;
    groups[group] = (groups[group] || 0) + raw;
    const weighted = raw * (weights[group] ?? 1.0);
    const mult = weights[group] ?? 1.0;
    const learned = Math.abs(mult - 1.0) > 0.05 ? ` [learned x${mult.toFixed(2)}]` : "";
    signals.push(`${label} (${weighted >= 0 ? "+" : ""}${weighted.toFixed(0)}${learned})`);
  };

  let score = 45.0;

  const liquidity = snap.liquidity_usd || 0;
  if (liquidity > 0) {
    const depth = clamp(6.0 * (Math.log10(liquidity) - Math.log10(25000)), 0.0, 18.0);
    apply("depth", depth, `liquidity $${Math.round(liquidity).toLocaleString()}`);
  }

  const volume = snap.volume_h24 || 0;
  if (liquidity > 0 && volume > 0) {
    const turnover = volume / liquidity;
    const churn = clamp(8.0 * (turnover - 1.0), 0.0, 12.0);
    if (churn > 0) {
      apply("turnover", churn, `turnover ${turnover.toFixed(1)}x`);
    } else if (turnover < 0.3) {
      apply("turnover", -6, `thin turnover ${turnover.toFixed(1)}x`);
    }
  }

  const buys = snap.buys_h1 || 0;
  const sells = snap.sells_h1 || 0;
  if (buys + sells >= 5) {
    const pressure = buys / (buys + sells);
    apply("pressure", clamp((pressure - 0.5) * 40.0, -12.0, 12.0), `1h buy pressure ${(pressure * 100).toFixed(0)}%`);
  }

  if (snap.change_h1 !== null && snap.change_h1 !== undefined) {
    apply("momentum1h", clamp(snap.change_h1 * 0.15, -12.0, 12.0), `1h ${snap.change_h1 >= 0 ? "+" : ""}${snap.change_h1.toFixed(0)}%`);
  }

  if (snap.change_h6 !== null && snap.change_h6 !== undefined) {
    apply("momentum6h", clamp(snap.change_h6 * 0.05, -8.0, 8.0), `6h ${snap.change_h6 >= 0 ? "+" : ""}${snap.change_h6.toFixed(0)}%`);
  }

  if (snap.change_h24 !== null && snap.change_h24 !== undefined) {
    if (snap.change_h24 > 800) {
      apply("mania", -20, `24h +${snap.change_h24.toFixed(0)}% mania`);
    } else if (snap.change_h24 > 300) {
      apply("mania", -10, `24h +${snap.change_h24.toFixed(0)}% late entry`);
    }
  }

  const age = snap.age_minutes;
  if (age !== null && age !== undefined) {
    if (age < 30) {
      apply("age", -8, `${age.toFixed(0)}m old, unproven`);
    } else if (age <= 6 * 60) {
      apply("age", 8, `${(age / 60).toFixed(1)}h old, sweet spot`);
    } else if (age <= 48 * 60) {
      apply("age", 4, `${(age / 60).toFixed(0)}h old`);
    } else if (age > 7 * 24 * 60) {
      apply("age", -6, "older than a week, story is stale");
    }
  }

  const extras: string[] = [];
  if (snap.boosts_active) {
    extras.push("boosted");
    apply("extras", 4, "boosted");
  }
  const socials = snap.socials.filter(Boolean);
  if (socials.length) {
    extras.push(...socials);
    apply("extras", 3, `socials: ${socials.slice(0, 3).join(", ")}`);
  }

  for (const group of GROUPS) {
    score += (groups[group] || 0) * (weights[group] ?? 1.0);
  }

  return {
    score: Math.round(clamp(score, 0.0, 100.0) * 10) / 10,
    signals,
    groups,
  };
}
