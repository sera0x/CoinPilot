"""Token credibility from RugCheck's public API.

Answers "what is this token's risk profile": mint and freeze authority, LP
lock, top-holder concentration. No key required, and a failed check degrades
to "unknown" — never a hard block on a flaky endpoint. The one non-negotiable
is a live mint or freeze authority, which means the deployer can print or
freeze everyone's bags.
"""

from __future__ import annotations

from coinpilot import http

BASE_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"


def _summary(mint: str, timeout: int = 12) -> dict:
    return http.get_json(BASE_URL.format(mint=mint), timeout=timeout, retries=1)


def check(mint: str) -> dict:
    """Return {ok, reject, adjust, note, risks}.

    reject is a reason string when the token must not be bought, else None.
    adjust is a score delta to apply when it survives.
    """

    try:
        payload = _summary(mint)
    except Exception as exc:  # noqa: BLE001 - an outage must not stop the filter
        return {
            "ok": False,
            "reject": None,
            "adjust": 0.0,
            "note": f"credibility unavailable ({str(exc)[:60]})",
            "risks": [],
        }

    raw_risks = payload.get("risks") or []
    risks = [item for item in raw_risks if isinstance(item, dict)]
    names = [str(item.get("name", "")).lower() for item in risks]
    levels = [str(item.get("level", "")).lower() for item in risks]

    if any("authority" in name and level == "danger" for name, level in zip(names, levels)):
        return {
            "ok": True,
            "reject": "rugcheck: live mint/freeze authority",
            "adjust": 0.0,
            "note": "mint/freeze authority active",
            "risks": names,
        }

    note_parts: list[str] = []
    adjust = 0.0

    score = payload.get("score_normalised")
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = None

    if score is not None:
        if score > 80:
            return {
                "ok": True,
                "reject": f"rugcheck risk score {score:.0f}/100",
                "adjust": 0.0,
                "note": f"rugcheck risk {score:.0f}/100",
                "risks": names,
            }
        if score > 60:
            adjust -= 10.0
            note_parts.append(f"rugcheck risk {score:.0f}/100 (-10)")
        elif score < 15:
            adjust += 5.0
            note_parts.append(f"rugcheck risk {score:.0f}/100 (+5)")
        else:
            note_parts.append(f"rugcheck risk {score:.0f}/100")

    danger_count = levels.count("danger")
    if danger_count:
        adjust -= 4.0 * danger_count
        note_parts.append(f"{danger_count} danger flag(s) (-4 each)")

    note = "; ".join(note_parts) or "clean rugcheck report"
    return {"ok": True, "reject": None, "adjust": adjust, "note": note, "risks": names}
