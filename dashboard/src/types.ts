export interface Account {
  starting_cash: number;
  cash: number;
  equity: number;
  unrealized: number;
  realized_today: number;
  realized_total: number;
  trades_today: number;
  day: string | null;
  kill: boolean;
}

export interface Position {
  id: number;
  symbol: string;
  token_address: string;
  entry_usd: number;
  entry_price: number;
  mark_price: number;
  value_usd: number;
  pnl_usd: number;
  pnl_pct: number;
  held_minutes: number;
  thesis: string;
}

export interface WatchItem {
  symbol: string;
  token_address: string;
  score: number;
  liquidity: number | null;
  volume: number | null;
  age: number | null;
  change: number | null;
  corroboration: number;
  sources: string;
  status: string;
  reason: string;
  credibility: string;
  flows: string;
}

export interface Trade {
  ts: number;
  side: string;
  symbol: string;
  usd: number;
  price: number;
  pnl: number | null;
  reason: string;
}

export interface Verdict {
  ts: number;
  symbol: string;
  action: string;
  score: number | null;
  source: string;
  thesis: string;
  confidence: number | null;
  risks: string[];
}

export interface GroupStat {
  pos_n: number;
  pos_sum: number;
  neg_n: number;
  neg_sum: number;
}

export interface Learning {
  mult: Record<string, number>;
  stats: Record<string, GroupStat>;
  n: number;
}

export interface WinStats {
  closed: number;
  wins: number;
  losses: number;
  win_rate: number;
  avg_win: number;
  avg_loss: number;
}

export interface Adjustment {
  ts: number;
  kind: string;
  value_from: number | null;
  value_to: number | null;
  reason: string;
}

export interface EdgeBucket {
  bucket: string;
  n: number;
  avg_outcome: number | null;
}

export interface Audit {
  adjustments: Adjustment[];
  edges: EdgeBucket[];
  band: {
    above: { n: number; avg_outcome: number | null };
    below: { n: number; avg_outcome: number | null };
  };
}

export interface DashboardData {
  generated: string;
  chain: string;
  account: Account;
  win_stats: WinStats;
  curve: number[];
  positions: Position[];
  watchlist: WatchItem[];
  trades: Trade[];
  verdicts: Verdict[];
  learning: Learning;
  audit: Audit;
  last_scan: number | null;
}
