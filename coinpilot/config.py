"""Configuration loading plus stable path helpers."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_NAME = "config.json"
EXAMPLE_NAME = "config.example.json"

DEFAULTS: dict = {
    "chain": "solana",
    "mode": "paper",
    "storage": {"db_path": "data/coinpilot.db"},
    "llm": {
        "enabled": True,
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
        "timeout_seconds": 60,
        "temperature": 0.2,
        "max_candidates_per_cycle": 3,
    },
    "scan": {
        "sources": ["news", "forums", "feeds"],
        "forum_subreddits": ["CryptoMoonShots", "memecoins", "SolanaMemeCoins", "Solana"],
        "news_feeds": [
            "https://www.coindesk.com/arc/outboundfeeds/rss/",
            "https://cointelegraph.com/rss",
            "https://decrypt.co/feed",
        ],
        "ignore_tickers": [
            "BTC",
            "ETH",
            "SOL",
            "USDC",
            "USDT",
            "BNB",
            "XRP",
            "ADA",
            "AVAX",
            "LINK",
            "DOT",
            "MATIC",
            "TRX",
        ],
        "metas": [],
        "user_agent": "",
        "max_leads": 120,
        "max_ticker_lookups": 25,
        "min_liquidity_usd": 25000,
        "min_volume_24h_usd": 50000,
        "min_age_minutes": 10,
        "max_age_hours": 720,
        "min_trades_24h": 20,
        "max_addresses": 60,
        "max_candidates": 8,
    },
    "strategy": {
        "buy_score": 62,
        "position_usd": 50,
        "min_position_usd": 5,
        "max_positions": 3,
        "take_profit_pct": 60,
        "stop_loss_pct": -25,
        "trailing_stop_pct": 20,
        "max_hold_minutes": 240,
    },
    "risk": {
        "starting_cash_usd": 1000,
        "max_position_pct_of_equity": 10,
        "max_exposure_pct": 60,
        "max_position_pct_of_liquidity": 0.02,
        "max_daily_loss_usd": 100,
        "max_trades_per_day": 20,
        "cooldown_minutes": 120,
        "kill_switch_file": "KILL",
    },
    "paper": {"fee_pct": 0.5, "slippage_pct": 1.5},
    "loop": {"interval_seconds": 300, "max_cycles": 0},
}


def default_config() -> dict:
    return copy.deepcopy(DEFAULTS)


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def resolve_config_path(path: str | None = None) -> Path | None:
    """Pick the config file to use: explicit, then env, then config.json, then the example."""

    if path:
        return Path(path).expanduser()
    from_env = os.environ.get("COINPILOT_CONFIG")
    if from_env:
        return Path(from_env).expanduser()
    local = PROJECT_ROOT / CONFIG_NAME
    if local.exists():
        return local
    example = PROJECT_ROOT / EXAMPLE_NAME
    if example.exists():
        return example
    return None


def load_config(path: str | None = None) -> dict:
    config_path = resolve_config_path(path)
    config = default_config()
    if config_path is not None and config_path.exists():
        with open(config_path, encoding="utf-8") as handle:
            config = _deep_merge(config, json.load(handle))
    return config


def resolve_path(value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    return (PROJECT_ROOT / candidate).resolve()


def db_path(config: dict) -> Path:
    return resolve_path(config.get("storage", {}).get("db_path") or DEFAULTS["storage"]["db_path"])


def kill_switch_path(config: dict) -> Path:
    name = config.get("risk", {}).get("kill_switch_file") or "KILL"
    return resolve_path(name)


def api_key(config: dict) -> str:
    env_name = config.get("llm", {}).get("api_key_env") or "OPENAI_API_KEY"
    return (os.environ.get(env_name) or "").strip()
