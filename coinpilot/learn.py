"""Self-learning: closed trades nudge the strategy's signal-group weights.

Every entry stores the raw per-group score contributions it had at buy time
(store.set_position_features). When the trade closes, groups whose signal
aligned with the outcome gain influence, groups whose signal contradicted it
lose some. Each multiplier is clamped to [0.5, 2.0] so a few lucky or unlucky
trades can never rewrite the strategy. Weights live in the `state` table under
'learn_state' and are shown on the dashboard.
"""

from __future__ import annotations

import json

GROUPS = ("depth", "turnover", "pressure", "momentum1h", "momentum6h", "mania", "age", "extras")

LEARNING_RATE = 0.02  # weight step per group per trade at +/-50% pnl
MIN_MULT = 0.5
MAX_MULT = 2.0


def default_state() -> dict:
    return {
        "mult": {group: 1.0 for group in GROUPS},
        "stats": {
            group: {"pos_n": 0, "pos_sum": 0.0, "neg_n": 0, "neg_sum": 0.0} for group in GROUPS
        },
        "n": 0,
    }


def load(connection) -> dict:
    row = connection.execute("SELECT value FROM state WHERE key = 'learn_state'").fetchone()
    state = default_state()
    if not row:
        return state

    try:
        data = json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return state
    if not isinstance(data, dict):
        return state

    for group in GROUPS:
        try:
            state["mult"][group] = max(MIN_MULT, min(MAX_MULT, float((data.get("mult") or {}).get(group, 1.0))))
        except (TypeError, ValueError):
            pass
        stat = (data.get("stats") or {}).get(group) or {}
        try:
            state["stats"][group] = {
                "pos_n": int(stat.get("pos_n", 0)),
                "pos_sum": float(stat.get("pos_sum", 0.0)),
                "neg_n": int(stat.get("neg_n", 0)),
                "neg_sum": float(stat.get("neg_sum", 0.0)),
            }
        except (TypeError, ValueError):
            pass

    try:
        state["n"] = max(0, int(data.get("n", 0)))
    except (TypeError, ValueError):
        pass
    return state


def save(connection, state: dict) -> None:
    from . import store

    store.set_state(connection, {"learn_state": json.dumps(state)})


def apply_outcome(state: dict, features: dict, pnl_pct: float) -> None:
    """Grade the entry-time signal contributions against the realized percent."""

    pnl = max(-50.0, min(50.0, float(pnl_pct or 0.0)))
    state["n"] = int(state.get("n", 0)) + 1

    for group in GROUPS:
        try:
            contribution = float((features or {}).get(group, 0.0))
        except (TypeError, ValueError):
            continue
        if abs(contribution) < 0.01:
            continue

        stat = state["stats"][group]
        if contribution > 0:
            stat["pos_n"] += 1
            stat["pos_sum"] += pnl
        else:
            stat["neg_n"] += 1
            stat["neg_sum"] += pnl

        # Signal said "yes" (contribution > 0) and the trade won -> trust it more.
        step = LEARNING_RATE * (pnl / 10.0) * (1.0 if contribution > 0 else -1.0)
        state["mult"][group] = max(MIN_MULT, min(MAX_MULT, state["mult"][group] + step))
