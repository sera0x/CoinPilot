-- CoinPilot on Cloudflare D1. Apply with:
--   npx wrangler d1 execute coinpilot --remote --file=schema.sql

CREATE TABLE IF NOT EXISTS state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token_address TEXT NOT NULL,
  symbol TEXT NOT NULL,
  pair_address TEXT,
  qty REAL NOT NULL,
  entry_price REAL NOT NULL,
  entry_usd REAL NOT NULL,
  peak_price REAL NOT NULL,
  opened_at REAL NOT NULL,
  closed_at REAL,
  close_price REAL,
  close_usd REAL,
  pnl_usd REAL,
  status TEXT NOT NULL DEFAULT 'open',
  thesis TEXT,
  features TEXT,
  mark_price REAL,
  mark_ts REAL
);

CREATE INDEX IF NOT EXISTS idx_positions_status ON positions (status);

CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  token_address TEXT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  qty REAL NOT NULL,
  price REAL NOT NULL,
  usd REAL NOT NULL,
  fee_usd REAL NOT NULL,
  reason TEXT,
  pnl_usd REAL
);

CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades (ts);

CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  token_address TEXT,
  symbol TEXT,
  stage TEXT NOT NULL,
  payload TEXT NOT NULL
);

-- Latest scan snapshot, rewritten every cycle. Powers the dashboard's token table.
CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  token_address TEXT NOT NULL,
  symbol TEXT NOT NULL,
  score REAL,
  corroboration INTEGER,
  sources TEXT,
  snapshot TEXT NOT NULL,
  rule TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'scanned'
);

-- One row per cron cycle, for the dashboard's activity feed.
CREATE TABLE IF NOT EXISTS cycles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  plan TEXT NOT NULL,
  leads_in INTEGER,
  priced INTEGER,
  candidates INTEGER,
  opened INTEGER,
  closed INTEGER,
  equity_usd REAL,
  note TEXT
);
