"""Sub-agent: the only process that writes positions, through the paper broker.

Opening a position also stores its entry-time signal contributions, and
closing one feeds the outcome back to the learner (coinpilot.learn).
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coinpilot import agentio, broker, learn, market, store
from coinpilot import config as config_mod


def _close(config: dict, connection, payload: dict) -> dict:
    position_id = int(payload["position_id"])
    position = next((item for item in store.open_positions(connection) if item["id"] == position_id), None)
    if position is None:
        return {"ok": True, "action": "close", "skipped": "position is not open"}

    snap = payload.get("snapshot")
    if not snap or not snap.get("price_usd"):
        snap = market.token_snapshot(config["chain"], position["token_address"])
    if not snap or not snap.get("price_usd"):
        return {"ok": True, "action": "close", "skipped": "no price available"}

    result = broker.sell(connection, position, snap, config, payload.get("reason") or "exit rule")

    # Self-learning: grade the entry-time signal contributions against the result.
    features = store.get_position_features(connection, position_id)
    if features:
        state = learn.load(connection)
        learn.apply_outcome(state, features, float(result.get("pnl_pct") or 0.0))
        learn.save(connection, state)

    return {"ok": True, "action": "close", **result}


def _open(config: dict, connection, payload: dict) -> dict:
    snap = payload.get("snapshot")
    if not snap or not snap.get("price_usd"):
        return {"ok": False, "error": "candidate has no price"}
    size = float(payload.get("size_usd") or 0.0)
    if size <= 0:
        return {"ok": False, "error": "size_usd must be positive"}
    result = broker.buy(connection, snap, size, config, payload.get("reason") or "")

    features = payload.get("features")
    if isinstance(features, dict) and features:
        store.set_position_features(connection, int(result["position_id"]), features)

    return {"ok": True, "action": "open", **result}


def handler(payload: dict) -> dict:
    config = config_mod.load_config()
    connection = store.connect(config_mod.db_path(config))
    try:
        action = payload.get("action")
        if action == "open":
            return _open(config, connection, payload)
        if action == "close":
            return _close(config, connection, payload)
        raise ValueError(f"unknown action {action!r}")
    finally:
        connection.close()


if __name__ == "__main__":
    agentio.serve(handler)
