"""Sub-agent: the hard limits, enforced as pure arithmetic on a supplied state."""

from __future__ import annotations

import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coinpilot import agentio
from coinpilot import config as config_mod


def blocked(reason: str, notes: list[str]) -> dict:
    return {"ok": True, "approved": False, "reason": reason, "size_usd": 0.0, "notes": notes}


def handler(payload: dict) -> dict:
    config = config_mod.load_config()
    strategy = config["strategy"]
    risk = config["risk"]

    snap = payload.get("snapshot") or {}
    decision = payload.get("decision") or {}
    state = payload.get("state") or {}
    notes: list[str] = []

    token = snap.get("token_address")
    if not token:
        return blocked("candidate has no token address", notes)

    if decision.get("action") != "buy":
        return blocked(f"analyst says {decision.get('action') or 'nothing'}", notes)

    score = float(decision.get("score") or 0.0)
    threshold = float(strategy["buy_score"])
    if score < threshold:
        return blocked(f"score {score:.0f} below threshold {threshold:.0f}", notes)

    open_positions = state.get("open_positions") or []
    if len(open_positions) >= int(strategy["max_positions"]):
        return blocked(f"position slots full ({len(open_positions)})", notes)
    if any(position.get("token_address") == token for position in open_positions):
        return blocked("already holding this token", notes)

    cash = float(state.get("cash_usd") or 0.0)
    equity = float(state.get("equity_usd") or 0.0)
    exposure = float(state.get("exposure_usd") or 0.0)
    if equity <= 0:
        return blocked("equity is zero", notes)
    if cash <= 0:
        return blocked("no paper cash left", notes)

    realized_today = float(state.get("realized_pnl_today") or 0.0)
    if realized_today <= -abs(float(risk["max_daily_loss_usd"])):
        return blocked(f"daily loss limit hit (${realized_today:.2f})", notes)

    trades_today = int(float(state.get("trades_today") or 0))
    if trades_today >= int(risk["max_trades_per_day"]):
        return blocked(f"daily trade cap hit ({trades_today})", notes)

    cooldown = float(risk["cooldown_minutes"])
    last_touched = (state.get("cooldowns") or {}).get(token)
    if last_touched:
        elapsed = (time.time() - float(last_touched)) / 60.0
        if elapsed < cooldown:
            return blocked(f"cooldown for this token ({elapsed:.0f}m of {cooldown:.0f}m)", notes)

    per_position = equity * float(risk["max_position_pct_of_equity"]) / 100.0
    size = min(float(strategy["position_usd"]), per_position, cash)
    notes.append(f"equity ${equity:.2f} allows ${per_position:.2f} per position")

    liquidity = float(snap.get("liquidity_usd") or 0.0)
    liquidity_cap = liquidity * float(risk["max_position_pct_of_liquidity"])
    if liquidity_cap > 0 and size > liquidity_cap:
        size = liquidity_cap
        notes.append(f"capped to {risk['max_position_pct_of_liquidity']:.1%} of ${liquidity:,.0f} liquidity")

    exposure_room = equity * float(risk["max_exposure_pct"]) / 100.0 - exposure
    if size > exposure_room:
        size = max(exposure_room, 0.0)
        notes.append(f"capped by total exposure ceiling (${exposure_room:.2f} room)")

    minimum = float(strategy.get("min_position_usd") or 5.0)
    if size < minimum:
        return blocked(f"sized ${size:.2f}, below the ${minimum:.2f} minimum", notes)

    return {
        "ok": True,
        "approved": True,
        "reason": f"approved ${size:.2f} at score {score:.0f}",
        "size_usd": round(size, 2),
        "notes": notes,
    }


if __name__ == "__main__":
    agentio.serve(handler)
