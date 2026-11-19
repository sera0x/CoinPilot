"""Deterministic rule scoring, used directly and as context for the LLM analyst."""

from __future__ import annotations

import math


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


def rule_score(snap: dict, config: dict) -> dict:
    """Score a snapshot from 0 to 100 and explain the signals behind it."""

    score = 45.0
    signals: list[str] = []

    liquidity = snap.get("liquidity_usd") or 0.0
    if liquidity > 0:
        depth = _clamp(6.0 * (math.log10(liquidity) - math.log10(25_000)), 0.0, 18.0)
        score += depth
        signals.append(f"liquidity ${liquidity:,.0f} (+{depth:.0f})")

    volume = snap.get("volume_h24") or 0.0
    if liquidity > 0 and volume > 0:
        turnover = volume / liquidity
        churn = _clamp(8.0 * (turnover - 1.0), 0.0, 12.0)
        if churn > 0:
            score += churn
            signals.append(f"turnover {turnover:.1f}x (+{churn:.0f})")
        elif turnover < 0.3:
            score -= 6
            signals.append(f"thin turnover {turnover:.1f}x (-6)")

    buys = snap.get("buys_h1") or 0.0
    sells = snap.get("sells_h1") or 0.0
    if buys + sells >= 5:
        pressure = buys / (buys + sells)
        bias = _clamp((pressure - 0.5) * 40.0, -12.0, 12.0)
        score += bias
        signals.append(f"1h buy pressure {pressure:.0%} ({bias:+.0f})")

    change_h1 = snap.get("change_h1")
    if change_h1 is not None:
        momentum = _clamp(change_h1 * 0.15, -12.0, 12.0)
        score += momentum
        signals.append(f"1h {change_h1:+.0f}% ({momentum:+.0f})")

    change_h6 = snap.get("change_h6")
    if change_h6 is not None:
        momentum = _clamp(change_h6 * 0.05, -8.0, 8.0)
        score += momentum
        signals.append(f"6h {change_h6:+.0f}% ({momentum:+.0f})")

    change_h24 = snap.get("change_h24")
    if change_h24 is not None:
        if change_h24 > 800:
            score -= 20
            signals.append(f"24h {change_h24:+.0f}% mania (-20)")
        elif change_h24 > 300:
            score -= 10
            signals.append(f"24h {change_h24:+.0f}% late entry (-10)")

    age = snap.get("age_minutes")
    if age is not None:
        if age < 30:
            score -= 8
            signals.append(f"{age:.0f}m old, unproven (-8)")
        elif age <= 6 * 60:
            score += 8
            signals.append(f"{age / 60:.1f}h old, sweet spot (+8)")
        elif age <= 48 * 60:
            score += 4
            signals.append(f"{age / 60:.0f}h old (+4)")
        elif age > 7 * 24 * 60:
            score -= 6
            signals.append("older than a week, story is stale (-6)")

    if snap.get("boosts_active"):
        score += 4
        signals.append("boosted (+4)")

    socials = [entry for entry in (snap.get("socials") or []) if entry]
    if socials:
        score += 3
        signals.append(f"socials: {', '.join(socials[:3])} (+3)")

    return {
        "score": round(_clamp(score, 0.0, 100.0), 1),
        "signals": signals,
        "filters": {
            "liquidity_usd": liquidity,
            "volume_h24": volume,
            "age_hours": round(age / 60.0, 2) if age is not None else None,
        },
    }
