"""The auditor: runs after every cycle, finds flaws in its own numbers, and
changes a bounded parameter when the evidence is strong enough.

It reads three things:
  * realized trades (win rate, today's loss against the daily budget)
  * the archive (coinpilot.memory): what actually happened to tokens the
    bot priced at or above the current entry bar, versus below it
  * the learned signal weights, to report drift

It writes `param_overrides` into the state table; config.load_config applies
those overrides everywhere, so the instructor, filter, analyst and risk agent
all trade on the audited threshold. Every change is logged to the `adjustments`
table with its reason, and the dashboard shows the log.

Bounds: buy_score may drift at most +10 or -6 from the config base, one step
of 2 at a time, and at most once per 3 hours. A bot that could rewrite itself
freely would just be a fast way to break itself.
"""

from __future__ import annotations

import json
import time

AUDIT_COOLDOWN_MIN = 180
STEP = 2.0
MAX_RAISE = 10.0
MAX_DROP = 6.0

from . import memory, store  # noqa: E402


def load_overrides(state: dict) -> dict:
    try:
        data = json.loads(state.get("param_overrides") or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def review(settings: dict, connection) -> list[str]:
    rules = settings["strategy"]
    risk = settings["risk"]
    base = float(rules["buy_score"])

    state = store.get_state(connection)
    overrides = load_overrides(state)
    current = float(overrides.get("buy_score") or base)

    now = time.time()
    last = float(state.get("auditor_last_ts") or 0)
    cooled = now - last < AUDIT_COOLDOWN_MIN * 60

    band = memory.band_stats(connection, current)
    wins = store.win_stats(connection)
    learn_n = _learn_count(state)

    lines: list[str] = []
    reason = None
    direction = 0.0

    above = band["above"]
    if above["n"] >= 12 and above["avg_outcome"] is not None:
        if above["avg_outcome"] <= -12.0:
            reason = (
                f"archive: {above['n']} studied tokens at/above score {current:.0f} "
                f"averaged {above['avg_outcome']:+.1f}% — the bar was too low"
            )
            direction = +STEP
        elif above["avg_outcome"] >= 10.0 and current > base:
            reason = (
                f"archive: {above['n']} studied tokens at/above score {current:.0f} "
                f"averaged {above['avg_outcome']:+.1f}% — easing back toward base"
            )
            direction = -STEP

    loss = float(state.get("realized_pnl_today") or 0.0)
    budget = abs(float(risk["max_daily_loss_usd"]))
    if direction == 0 and budget and loss <= -0.5 * budget:
        reason = f"down {loss:+.2f} today (half the ${budget:.0f} budget) — raising the bar"
        direction = +STEP

    if direction and cooled:
        lines.append(f"auditor: change skipped, cooldown ({AUDIT_COOLDOWN_MIN}m between changes)")
        direction = 0.0

    if direction:
        target = round(max(base - MAX_DROP, min(base + MAX_RAISE, current + direction)), 1)
        overrides["buy_score"] = target
        store.set_state(
            connection,
            {"param_overrides": json.dumps(overrides), "auditor_last_ts": f"{now:.0f}"},
        )
        store.record_adjustment(
            connection,
            kind="buy_score",
            value_from=current,
            value_to=target,
            reason=reason or "",
        )
        store.record_decision(
            connection,
            "auditor",
            {"changed": "buy_score", "from": current, "to": target, "reason": reason},
        )
        lines.append(f"auditor: buy_score {current:.0f} -> {target:.0f} ({reason})")
    elif not reason:
        drift = _weight_drift(state)
        lines.append(
            f"auditor: no flaw worth changing "
            f"(archive: {above['n']} studied at/above the bar, "
            f"win rate {wins['win_rate']:.0f}% over {wins['closed']} closed)"
            + (f"; {drift}" if drift else "")
        )
    else:
        lines.append(f"auditor: evidence says change, but the cooldown holds — next window in ~{int((AUDIT_COOLDOWN_MIN * 60 - (now - last)) / 60)}m")

    return lines


def _learn_count(state: dict) -> int:
    try:
        return max(0, int(json.loads(state.get("learn_state") or "{}").get("n", 0)))
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0


def _weight_drift(state: dict) -> str:
    try:
        mult = json.loads(state.get("learn_state") or "{}").get("mult") or {}
    except json.JSONDecodeError:
        return ""
    usable = {key: float(value) for key, value in mult.items() if isinstance(value, (int, float))}
    if not usable:
        return ""
    strongest = max(usable, key=lambda k: usable[k])
    weakest = min(usable, key=lambda k: usable[k])
    return (
        f"signal drift: {strongest} x{usable[strongest]:.2f} trusted most, "
        f"{weakest} x{usable[weakest]:.2f} least"
    )
