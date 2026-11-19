/** Defaults mirror coinpilot/config.py; CONFIG_JSON (a var) overrides them. */

export interface ScanConfig {
  sources: string[];
  forum_subreddits: string[];
  news_feeds: string[];
  ignore_tickers: string[];
  metas: string[];
  user_agent: string;
  max_leads: number;
  max_ticker_lookups: number;
  min_liquidity_usd: number;
  min_volume_24h_usd: number;
  min_age_minutes: number;
  max_age_hours: number;
  min_trades_24h: number;
  max_addresses: number;
  max_candidates: number;
}

export interface StrategyConfig {
  buy_score: number;
  position_usd: number;
  min_position_usd: number;
  max_positions: number;
  take_profit_pct: number;
  stop_loss_pct: number;
  trailing_stop_pct: number;
  max_hold_minutes: number;
}

export interface RiskConfig {
  starting_cash_usd: number;
  max_position_pct_of_equity: number;
  max_exposure_pct: number;
  max_position_pct_of_liquidity: number;
  max_daily_loss_usd: number;
  max_trades_per_day: number;
  cooldown_minutes: number;
}

export interface LlmConfig {
  base_url: string;
  model: string;
  temperature: number;
  max_candidates_per_cycle: number;
}

export interface Config {
  chain: string;
  scan: ScanConfig;
  strategy: StrategyConfig;
  risk: RiskConfig;
  llm: LlmConfig;
  paper: { fee_pct: number; slippage_pct: number };
}

const DEFAULTS: Config = {
  chain: "solana",
  scan: {
    sources: ["news", "forums", "feeds"],
    forum_subreddits: ["CryptoMoonShots", "memecoins", "SolanaMemeCoins", "Solana"],
    news_feeds: [
      "https://www.coindesk.com/arc/outboundfeeds/rss/",
      "https://cointelegraph.com/rss",
      "https://decrypt.co/feed",
    ],
    ignore_tickers: [
      "BTC", "ETH", "SOL", "USDC", "USDT", "BNB", "XRP",
      "ADA", "AVAX", "LINK", "DOT", "MATIC", "TRX",
    ],
    metas: [],
    user_agent: "",
    max_leads: 60, // Workers free tier allows ~50 subrequests per invocation: stay small
    max_ticker_lookups: 8,
    min_liquidity_usd: 25000,
    min_volume_24h_usd: 50000,
    min_age_minutes: 10,
    max_age_hours: 720,
    min_trades_24h: 20,
    max_addresses: 30,
    max_candidates: 8,
  },
  strategy: {
    buy_score: 62,
    position_usd: 50,
    min_position_usd: 5,
    max_positions: 3,
    take_profit_pct: 60,
    stop_loss_pct: -25,
    trailing_stop_pct: 20,
    max_hold_minutes: 240,
  },
  risk: {
    starting_cash_usd: 1000,
    max_position_pct_of_equity: 10,
    max_exposure_pct: 60,
    max_position_pct_of_liquidity: 0.02,
    max_daily_loss_usd: 100,
    max_trades_per_day: 20,
    cooldown_minutes: 120,
  },
  llm: {
    base_url: "https://api.openai.com/v1",
    model: "gpt-4o-mini",
    temperature: 0.2,
    max_candidates_per_cycle: 3,
  },
  paper: { fee_pct: 0.5, slippage_pct: 1.5 },
};

type Any = Record<string, any>;

function deepMerge(base: Any, override: Any): Any {
  const out: Any = { ...base };
  for (const [key, value] of Object.entries(override)) {
    if (value && typeof value === "object" && !Array.isArray(value) && out[key] && typeof out[key] === "object" && !Array.isArray(out[key])) {
      out[key] = deepMerge(out[key], value);
    } else {
      out[key] = value;
    }
  }
  return out;
}

export function loadConfig(env: Env): Config {
  let override: Any = {};
  const raw = (env.CONFIG_JSON || "").trim();
  if (raw) {
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object") override = parsed;
    } catch {
      // a malformed CONFIG_JSON falls back to defaults rather than killing the cron
    }
  }
  const merged = deepMerge(DEFAULTS as unknown as Any, override) as unknown as Config;
  merged.chain = (env.CHAIN || merged.chain || "solana").toLowerCase();
  if (env.LLM_BASE_URL) merged.llm.base_url = env.LLM_BASE_URL;
  if (env.LLM_MODEL) merged.llm.model = env.LLM_MODEL;
  return merged;
}
