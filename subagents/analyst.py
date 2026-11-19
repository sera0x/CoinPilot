"""Sub-agent: ask the model whether a candidate is worth a paper position."""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coinpilot import agentio, strategy
from coinpilot import config as config_mod
from coinpilot.llm import LLM

SYSTEM = (
    "You are the analyst sub-agent of a Solana meme coin paper-trading bot. "
    "You receive one token's live market snapshot and a deterministic rule score. "
    "Meme coins are reflexive and most go to zero: prefer clean depth, genuine buy pressure, "
    "and a readable narrative; be wary of one-sided volume, entries already up hundreds of percent "
    "in 24 hours, and tokens old enough that the story has gone stale. "
    "Never invent facts that are not in the snapshot. "
    'Reply with JSON only: {"action":"buy" or "skip","score":0-100,"confidence":0-100,'
    '"thesis":"one or two sentences","risks":["short strings"]}'
)

PROMPT_FIELDS = (
    "symbol",
    "name",
    "dex",
    "price_usd",
    "liquidity_usd",
    "fdv",
    "market_cap",
    "volume_m5",
    "volume_h1",
    "volume_h24",
    "change_m5",
    "change_h1",
    "change_h6",
    "change_h24",
    "buys_h1",
    "sells_h1",
    "buys_h24",
    "sells_h24",
    "age_minutes",
    "boosts_active",
    "socials",
)


def _clamp(value, low: float, high: float, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def normalise(raw: dict, config: dict, rule: dict) -> dict:
    action = str(raw.get("action") or "").strip().lower()
    if action not in {"buy", "skip"}:
        action = "buy" if _clamp(raw.get("score"), 0, 100, rule["score"]) >= float(config["strategy"]["buy_score"]) else "skip"

    thesis = str(raw.get("thesis") or "").strip()[:600]
    risks = raw.get("risks")
    if isinstance(risks, str):
        risks = [risks]
    if not isinstance(risks, list):
        risks = []

    return {
        "action": action,
        "score": _clamp(raw.get("score"), 0.0, 100.0, rule["score"]),
        "confidence": _clamp(raw.get("confidence"), 0.0, 100.0, 50.0),
        "thesis": thesis,
        "risks": [str(risk)[:200] for risk in risks][:5],
    }


def build_prompt(snap: dict, rule: dict, config: dict) -> str:
    compact = {key: snap.get(key) for key in PROMPT_FIELDS}
    brief = {
        "chain": config["chain"],
        "snapshot": compact,
        "rule_score": rule["score"],
        "rule_signals": rule["signals"],
        "buy_threshold": config["strategy"]["buy_score"],
        "planned_position_usd": config["strategy"]["position_usd"],
        "take_profit_pct": config["strategy"]["take_profit_pct"],
        "stop_loss_pct": config["strategy"]["stop_loss_pct"],
    }
    return "Evaluate this candidate for a short holding period.\n" + json.dumps(brief, indent=2, default=str)


def handler(payload: dict) -> dict:
    config = config_mod.load_config()
    snap = payload.get("snapshot") or {}
    rule = payload.get("rule") or strategy.rule_score(snap, config)

    decision = None
    error = None
    client = LLM(config)
    if client.available:
        try:
            decision = normalise(client.chat_json(SYSTEM, build_prompt(snap, rule, config)), config, rule)
        except Exception as exc:  # noqa: BLE001 - rules are the fallback
            error = str(exc)
            agentio.log(f"analyst model call failed, using rules: {exc}")

    if decision is None:
        threshold = float(config["strategy"]["buy_score"])
        decision = {
            "action": "buy" if rule["score"] >= threshold else "skip",
            "score": rule["score"],
            "confidence": 50.0,
            "thesis": "rule engine: " + "; ".join(rule["signals"][:3]),
            "risks": [],
            "source": "rules",
        }
    else:
        decision["source"] = "llm"

    if error:
        decision["llm_error"] = error[:300]
    decision["rule_score"] = rule["score"]
    decision["rule_signals"] = rule["signals"]
    return {"ok": True, **decision}


if __name__ == "__main__":
    agentio.serve(handler)
