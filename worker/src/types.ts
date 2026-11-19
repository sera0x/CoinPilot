/** Shared types for the CoinPilot worker. */

export interface Env {
  DB: D1Database;
  ASSETS: Fetcher;
  CHAIN?: string;
  CONFIG_JSON?: string;
  LLM_BASE_URL?: string;
  LLM_MODEL?: string;
  LLM_API_KEY?: string;
  DASHBOARD_TOKEN?: string;
}

/** A flattened DexScreener pair: the fields the strategy reads. */
export interface Snapshot {
  chain?: string;
  dex?: string;
  token_address?: string;
  symbol?: string;
  name?: string;
  quote_symbol?: string;
  pair_address?: string;
  price_usd: number | null;
  liquidity_usd: number | null;
  fdv: number | null;
  market_cap: number | null;
  volume_m5: number | null;
  volume_h1: number | null;
  volume_h24: number | null;
  change_m5: number | null;
  change_h1: number | null;
  change_h6: number | null;
  change_h24: number | null;
  buys_h1: number | null;
  sells_h1: number | null;
  buys_h24: number | null;
  sells_h24: number | null;
  pair_created_at: number | null;
  age_minutes: number | null;
  boosts_active: number | null;
  socials: string[];
  url?: string;
}

export interface Lead {
  source: string;
  title: string;
  url: string;
  mints: string[];
  tickers: string[];
  published_at?: string | null;
}

/** One signal group the rule score touches; each has a learned multiplier. */
export type SignalGroup =
  | "depth"
  | "turnover"
  | "pressure"
  | "momentum1h"
  | "momentum6h"
  | "mania"
  | "age"
  | "extras";

export type Weights = Record<SignalGroup, number>;

export interface GroupStat {
  pos_n: number;
  pos_sum: number;
  neg_n: number;
  neg_sum: number;
}

export interface LearnState {
  mult: Weights;
  stats: Record<SignalGroup, GroupStat>;
  n: number;
}

export interface RuleScore {
  score: number;
  signals: string[];
  groups: Partial<Record<SignalGroup, number>>;
}

export interface Candidate {
  snapshot: Snapshot;
  rule: RuleScore;
  corroboration: number;
  sources: string[];
}

export interface Decision {
  action: "buy" | "skip";
  score: number;
  confidence: number;
  thesis: string;
  risks: string[];
  source: "llm" | "rules";
  rule_score: number;
  rule_signals: string[];
  llm_error?: string;
}

export interface Plan {
  directive: string;
  sources: string[];
  max_candidates: number;
  max_candidates_per_cycle: number;
  risk_posture: "normal" | "defensive" | "pause";
  notes: string[];
  slots_free?: number;
  source_of_truth: "llm" | "rules";
}

export interface PositionRow {
  id: number;
  token_address: string;
  symbol: string;
  pair_address: string | null;
  qty: number;
  entry_price: number;
  entry_usd: number;
  peak_price: number;
  opened_at: number;
  status: string;
  thesis: string | null;
  features: string | null;
  mark_price: number | null;
  mark_ts: number | null;
}

export interface PortfolioState {
  cash_usd: number;
  equity_usd: number;
  exposure_usd: number;
  unrealized_pnl_usd: number;
  open_positions: { id: number; token_address: string; symbol: string; entry_usd: number }[];
  trades_today: number;
  realized_pnl_today: number;
  cooldowns: Record<string, number>;
  marks: Record<string, Snapshot>;
  now: number;
}
