"""Deterministic rule scoring, used directly and as context for the LLM analyst.

rule_score also returns the raw per-signal-group contributions ("groups") so
closed trades can grade them later (coinpilot.learn). An optional learned
weights dict scales each group's contribution.
"""

from __future__ import annotations

import math

from . import learn


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def disqualify(snap: dict, config: dict) -> str | None:
    """Reason to skip a token entirely, or None when it passes the basic filters."""

    scan = config["scan"]
    price = snap.get("price_usd")
    if not price or price <= 0:
        return "no price"

    liquidity = snap.get("liquidity_usd") or 0.0
    if liquidity < scan["min_liquidity_usd"]:
        return f"liquidity ${liquidity:,.0f} under ${scan['min_liquidity_usd']:,.0f}"

    volume = snap.get("volume_h24") or 0.0
    if volume < scan["min_volume_24h_usd"]:
        return f"24h volume ${volume:,.0f} under ${scan['min_volume_24h_usd']:,.0f}"

    trades = (snap.get("buys_h24") or 0) + (snap.get("sells_h24") or 0)
    if trades < scan["min_trades_24h"]:
        return f"only {trades:.0f} trades in 24h"

    age = snap.get("age_minutes")
    if age is not None:
        if age < scan["min_age_minutes"]:
            return f"too new ({age:.0f}m old)"
        if age > scan["max_age_hours"] * 60:
            return f"too old ({age / 60:.0f}h)"

    return None


def rule_score(snap: dict, config: dict, weights: dict | None = None) -> dict:
    """Score a snapshot from 0 to 100 and explain the signals behind it.

    weights: optional learned per-group multipliers (learn.load()["mult"]).
    """

    mult = {group: 1.0 for group in learn.GROUPS}
    for group, value in (weights or {}).items():
        if group in mult:
            try:
                mult[group] = float(value)
            except (TypeError, ValueError):
                pass

    score = 45.0
    groups: dict[str, float] = {}
    signals: list[str] = []

    def apply(group: str, raw: float, label: str) -> None:
        nonlocal score
        if abs(raw) < 0.01:
            return
        groups[group] = groups.get(group, 0.0) + raw
        weighted = raw * mult.get(group, 1.0)
        score += weighted
        learned = ""
        if abs(mult.get(group, 1.0) - 1.0) > 0.05:
            learned = f" [learned x{mult[group]:.2f}]"
        signals.append(f"{label} ({weighted:+.0f}{learned})")

    liquidity = snap.get("liquidity_usd") or 0.0
    if liquidity > 0:
        depth = _clamp(6.0 * (math.log10(liquidity) - math.log10(25_000)), 0.0, 18.0)
        apply("depth", depth, f"liquidity ${liquidity:,.0f}")

    volume = snap.get("volume_h24") or 0.0
    if liquidity > 0 and volume > 0:
        turnover = volume / liquidity
        churn = _clamp(8.0 * (turnover - 1.0), 0.0, 12.0)
        if churn > 0:
            apply("turnover", churn, f"turnover {turnover:.1f}x")
        elif turnover < 0.3:
            apply("turnover", -6, f"thin turnover {turnover:.1f}x")

    buys = snap.get("buys_h1") or 0.0
    sells = snap.get("sells_h1") or 0.0
    if buys + sells >= 5:
        pressure = buys / (buys + sells)
        bias = _clamp((pressure - 0.5) * 40.0, -12.0, 12.0)
        apply("pressure", bias, f"1h buy pressure {pressure:.0%}")

    change_h1 = snap.get("change_h1")
    if change_h1 is not None:
        apply("momentum1h", _clamp(change_h1 * 0.15, -12.0, 12.0), f"1h {change_h1:+.0f}%")

    change_h6 = snap.get("change_h6")
    if change_h6 is not None:
        apply("momentum6h", _clamp(change_h6 * 0.05, -8.0, 8.0), f"6h {change_h6:+.0f}%")

    change_h24 = snap.get("change_h24")
    if change_h24 is not None:
        if change_h24 > 800:
            apply("mania", -20, f"24h {change_h24:+.0f}% mania")
        elif change_h24 > 300:
            apply("mania", -10, f"24h {change_h24:+.0f}% late entry")

    age = snap.get("age_minutes")
    if age is not None:
        if age < 30:
            apply("age", -8, f"{age:.0f}m old, unproven")
        elif age <= 6 * 60:
            apply("age", 8, f"{age / 60:.1f}h old, sweet spot")
        elif age <= 48 * 60:
            apply("age", 4, f"{age / 60:.0f}h old")
        elif age > 7 * 24 * 60:
            apply("age", -6, "older than a week, story is stale")

    if snap.get("boosts_active"):
        apply("extras", 4, "boosted")

    socials = [entry for entry in (snap.get("socials") or []) if entry]
    if socials:
        apply("extras", 3, f"socials: {', '.join(socials[:3])}")

    return {
        "score": round(_clamp(score, 0.0, 100.0), 1),
        "signals": signals,
        "groups": {group: round(value, 3) for group, value in groups.items()},
        "filters": {
            "liquidity_usd": liquidity,
            "volume_h24": volume,
            "age_hours": round(age / 60.0, 2) if age is not None else None,
        },
    }
