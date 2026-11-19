"""JSON in, JSON out plumbing shared by every sub-agent process."""

from __future__ import annotations

import json
import sys


def log(message: str) -> None:
    """Diagnostics go to stderr so stdout stays machine readable."""

    print(message, file=sys.stderr, flush=True)


def read_payload() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    return json.loads(raw)


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, default=str))
    sys.stdout.write("\n")
    sys.stdout.flush()


def serve(handler) -> None:
    """Run a sub-agent: read one payload, return one result, never crash the caller."""

    try:
        result = handler(read_payload())
    except Exception as exc:  # noqa: BLE001 - reported to the orchestrator as data
        emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        raise SystemExit(0) from None
    if not isinstance(result, dict):
        result = {"value": result}
    result.setdefault("ok", True)
    emit(result)
