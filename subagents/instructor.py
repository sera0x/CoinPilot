"""Sub-agent: read the portfolio, then set the plan every other agent follows."""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coinpilot import agentio
from coinpilot import config as config_mod
from coinpilot.llm import LLM

ALLOWED_SOURCES = ("news", "forums", "feeds")

SYSTEM = (
    "You are the instructor sub-agent of a Solana meme coin paper-trading desk. "
    "You command four downstream agents: scouts that hunt leads on news sites and forums, "
    "a filter that resolves and screens them, an analyst that judges a setup and gives a thesis, "
    "and a risk manager that refuses anything outside its limits. "
    "You do not pick tokens yourself and you never see prices; you decide the plan for this cycle: "
    "which discovery sources to run, how many candidates to keep, how many entries to attempt, "
    "one directive sentence every downstream agent must respect, and the risk posture. "
    "Be conservative when the portfolio is losing, over-exposed or out of slots. "
    'Reply with JSON only: {"directive": "...", "sources": ["news","forums","feeds"], '
    '"max_candidates": 1-20, "max_candidates_per_cycle": 0-5, '
    '"risk_posture": "normal" or "defensive" or "pause", "notes": ["short strings"]}'
)


def _clamp_int(value, low: int, high: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def rule_plan(config: dict, state: dict) -> dict:
    scan = config["scan"]
    strategy = config["strategy"]
    risk = config["risk"]

    sources = [source for source in (scan.get("sources") or []) if source in ALLOWED_SOURCES]
    if not sources:
        sources = list(ALLOWED_SOURCES)

    slots = int(strategy["max_positions"]) - len(state.get("open_positions") or [])
    attempts = int(config["llm"].get("max_candidates_per_cycle") or 3)
    max_candidates = int(scan.get("max_candidates") or 8)
    posture = "normal"
    notes: list[str] = []

    loss_today = float(state.get("realized_pnl_today") or 0.0)
    budget = abs(float(risk["max_daily_loss_usd"]))
    if budget and loss_today <= -budget:
        posture = "pause"
        attempts = 0
        notes.append(f"daily loss limit spent ({loss_today:.2f})")
    elif budget and loss_today <= -0.5 * budget:
        posture = "defensive"
        attempts = max(1, attempts // 2)
        max_candidates = max(3, max_candidates // 2)
        notes.append(f"half the daily loss budget is gone ({loss_today:.2f})")

    if slots <= 0:
        attempts = 0
        notes.append("no free position slots")

    equity = float(state.get("equity_usd") or 0.0)
    exposure = float(state.get("exposure_usd") or 0.0)
    if equity > 0 and exposure / equity >= 0.5 and posture == "normal":
        posture = "defensive"
        notes.append("half the equity is already committed")

    directive = {
        "normal": "Standard posture: hunt broadly, keep only setups that clear the filters, size at plan.",
        "defensive": "Defensive posture: fewer entries, stricter reading, no chasing already-pumped setups.",
        "pause": "Paused posture: scan and record only, open nothing this cycle.",
    }[posture]

    return {
        "directive": directive,
        "sources": sources,
        "max_candidates": max_candidates,
        "max_candidates_per_cycle": max(0, attempts),
        "risk_posture": posture,
        "notes": notes,
        "slots_free": slots,
        "source_of_truth": "rules",
    }


def sanitise(raw: dict, config: dict, base: dict) -> dict:
    sources = [source for source in (raw.get("sources") or []) if source in ALLOWED_SOURCES]
    if not sources:
        sources = list(base["sources"])

    max_candidates = _clamp_int(raw.get("max_candidates"), 1, int(config["scan"]["max_candidates"]), base["max_candidates"])
    attempts = _clamp_int(
        raw.get("max_candidates_per_cycle"),
        0,
        int(config["llm"].get("max_candidates_per_cycle") or 3),
        base["max_candidates_per_cycle"],
    )

    posture = str(raw.get("risk_posture") or "normal").strip().lower()
    if posture not in {"normal", "defensive", "pause"}:
        posture = "normal"
    if posture == "pause":
        attempts = 0

    notes = [str(note)[:200] for note in (raw.get("notes") or []) if note][:6]
    directive = str(raw.get("directive") or base["directive"]).strip()[:400]

    return {
        "directive": directive,
        "sources": sources,
        "max_candidates": max_candidates,
        "max_candidates_per_cycle": attempts,
        "risk_posture": posture,
        "notes": notes,
    }


def build_prompt(config: dict, state: dict, base: dict) -> str:
    brief = {
        "chain": config["chain"],
        "available_sources": list(ALLOWED_SOURCES),
        "knobs": {
            "max_positions": config["strategy"]["max_positions"],
            "buy_score": config["strategy"]["buy_score"],
            "position_usd": config["strategy"]["position_usd"],
            "max_daily_loss_usd": config["risk"]["max_daily_loss_usd"],
            "max_candidates": config["scan"]["max_candidates"],
            "max_attempts": config["llm"].get("max_candidates_per_cycle"),
        },
        "portfolio": {
            key: state.get(key)
            for key in ("cash_usd", "equity_usd", "exposure_usd", "unrealized_pnl_usd", "trades_today", "realized_pnl_today")
        },
        "open_positions": [
            {"symbol": position.get("symbol"), "entry_usd": position.get("entry_usd")}
            for position in state.get("open_positions") or []
        ],
        "slots_free": base["slots_free"],
        "rules_plan": {
            "directive": base["directive"],
            "sources": base["sources"],
            "max_candidates": base["max_candidates"],
            "max_candidates_per_cycle": base["max_candidates_per_cycle"],
            "risk_posture": base["risk_posture"],
            "notes": base["notes"],
        },
    }
    return "Set this cycle's plan. The rules plan is a safe default you may tighten, not loosen.\n" + json.dumps(
        brief, indent=2, default=str
    )


def handler(payload: dict) -> dict:
    config = config_mod.load_config()
    state = payload.get("state") or {}
    plan = rule_plan(config, state)

    client = LLM(config)
    if client.available:
        try:
            raw = client.chat_json(SYSTEM, build_prompt(config, state, plan))
            plan.update(sanitise(raw, config, plan))
            plan["source_of_truth"] = "llm"
        except Exception as exc:  # noqa: BLE001 - the rules plan is good enough to trade on
            agentio.log(f"instructor model call failed, keeping the rules plan: {exc}")
            plan["notes"] = list(plan["notes"]) + [f"model unavailable: {str(exc)[:160]}"]

    return {"ok": True, **plan}


if __name__ == "__main__":
    agentio.serve(handler)
