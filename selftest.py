#!/usr/bin/env python3
"""Offline smoke test: no network, no API key, no real money.

Run: python3 selftest.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from coinpilot import broker, strategy, store
from coinpilot import config as config_mod

FAILURES = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global FAILURES
    if condition:
        print(f"[ ok ] {name}")
        return
    FAILURES += 1
    print(f"[FAIL] {name}{f' - {detail}' if detail else ''}")


def fake_snapshot(
    price: float = 0.00042,
    liquidity: float = 200_000.0,
    volume: float = 400_000.0,
    change_h1: float = 12.0,
    change_h6: float = 30.0,
    change_h24: float = 45.0,
    age: float = 90.0,
) -> dict:
    return {
        "chain": "solana",
        "dex": "raydium",
        "token_address": "TESTtoken1111111111111111111111111111111",
        "symbol": "TEST",
        "name": "Test Coin",
        "quote_symbol": "SOL",
        "pair_address": "TESTpair111111111111111111111111111111111",
        "price_usd": price,
        "liquidity_usd": liquidity,
        "fdv": 500_000.0,
        "market_cap": 400_000.0,
        "volume_m5": volume / 100.0,
        "volume_h1": volume / 12.0,
        "volume_h6": volume / 3.0,
        "volume_h24": volume,
        "change_m5": 1.0,
        "change_h1": change_h1,
        "change_h6": change_h6,
        "change_h24": change_h24,
        "buys_m5": 5,
        "sells_m5": 3,
        "buys_h1": 90,
        "sells_h1": 55,
        "buys_h24": 900,
        "sells_h24": 700,
        "pair_created_at": None,
        "age_minutes": age,
        "boosts_active": 1,
        "socials": ["twitter"],
        "url": "",
    }


def run_subagent(module: str, payload: dict, config_path: Path, timeout: int = 60) -> dict:
    environment = os.environ.copy()
    environment["COINPILOT_CONFIG"] = str(config_path)
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(config_mod.PROJECT_ROOT), environment.get("PYTHONPATH")) if value
    )
    process = subprocess.run(
        [sys.executable, "-m", module],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(config_mod.PROJECT_ROOT),
        env=environment,
        timeout=timeout,
    )
    stdout = (process.stdout or "").strip()
    if not stdout:
        raise RuntimeError(f"{module} produced no output: {(process.stderr or '').strip()[-500:]}")
    return json.loads(stdout.splitlines()[-1])


def test_rules(config: dict) -> None:
    print("\n-- rule engine --")
    good = fake_snapshot()
    check("a healthy snapshot passes the filters", strategy.disqualify(good, config) is None)

    rule = strategy.rule_score(good, config)
    check("rule score is in range", 0.0 <= rule["score"] <= 100.0, str(rule["score"]))
    check("rule score clears the buy threshold", rule["score"] >= config["strategy"]["buy_score"], str(rule["score"]))
    check("rule score explains itself", bool(rule["signals"]))

    thin = fake_snapshot(liquidity=1_000.0)
    check("thin liquidity is rejected", bool(strategy.disqualify(thin, config)))

    newborn = fake_snapshot(age=2.0)
    check("a two-minute-old token is rejected", bool(strategy.disqualify(newborn, config)))

    stale = fake_snapshot(age=60 * 24 * 400)
    check("a very old pair is rejected", bool(strategy.disqualify(stale, config)))

    mania = strategy.rule_score(fake_snapshot(change_h24=1_200.0), config)
    check("a 1200% day scores worse than a 45% day", mania["score"] < rule["score"])


def test_broker(config: dict, db_path: Path) -> None:
    print("\n-- paper broker --")
    connection = store.connect(db_path)
    try:
        store.bootstrap(connection, 1_000.0, "2026-09-22")
        snapshot = fake_snapshot()

        fill = broker.buy(connection, snapshot, 50.0, config, "selftest entry")
        check("buy opens a position", bool(store.open_positions(connection)))
        check("buy pays a fee", fill["fee_usd"] > 0, str(fill["fee_usd"]))
        check("entry slippage is applied", fill["fill_price"] > snapshot["price_usd"])
        check("cash is reduced by the full size", abs(store.cash(connection) - 950.0) < 1e-6, str(store.cash(connection)))

        position = store.open_positions(connection)[0]
        exited = broker.sell(connection, position, fake_snapshot(price=snapshot["price_usd"] * 2.0), config, "selftest exit")
        check("sell realizes a profit after a 2x", exited["pnl_usd"] > 0, str(exited["pnl_usd"]))
        check("cash grows on the exit", store.cash(connection) > 950.0, str(store.cash(connection)))
        check("the position is closed", not store.open_positions(connection))

        try:
            broker.buy(connection, snapshot, 10_000.0, config, "overspend")
            check("an overspend is refused", False, "no error raised")
        except broker.BrokerError:
            check("an overspend is refused", True)
    finally:
        connection.close()


def test_risk_agent(config_path: Path) -> None:
    print("\n-- risk sub-agent --")
    state = {
        "cash_usd": 950.0,
        "equity_usd": 1_000.0,
        "exposure_usd": 0.0,
        "open_positions": [],
        "trades_today": 0,
        "realized_pnl_today": 0.0,
        "cooldowns": {},
        "now": time.time(),
    }
    decision = {"action": "buy", "score": 80.0, "confidence": 70.0, "thesis": "selftest"}

    verdict = run_subagent("subagents.risk", {"snapshot": fake_snapshot(), "decision": decision, "state": state}, config_path)
    check("a good setup is approved", bool(verdict.get("approved")), str(verdict.get("reason")))
    check("approved size stays within the plan", 0 < verdict.get("size_usd", 0) <= 50.0, str(verdict.get("size_usd")))

    skipped = dict(decision, action="skip")
    verdict = run_subagent("subagents.risk", {"snapshot": fake_snapshot(), "decision": skipped, "state": state}, config_path)
    check("a skip decision is not traded", not verdict.get("approved"))

    weak = dict(decision, score=10.0)
    verdict = run_subagent("subagents.risk", {"snapshot": fake_snapshot(), "decision": weak, "state": state}, config_path)
    check("a low score is blocked", not verdict.get("approved"))

    tired = dict(state, trades_today=999)
    verdict = run_subagent("subagents.risk", {"snapshot": fake_snapshot(), "decision": decision, "state": tired}, config_path)
    check("the daily trade cap blocks entries", not verdict.get("approved"))

    sore = dict(state, realized_pnl_today=-5_000.0)
    verdict = run_subagent("subagents.risk", {"snapshot": fake_snapshot(), "decision": decision, "state": sore}, config_path)
    check("the daily loss limit blocks entries", not verdict.get("approved"))

    cooling = dict(state, cooldowns={fake_snapshot()["token_address"]: time.time()})
    verdict = run_subagent("subagents.risk", {"snapshot": fake_snapshot(), "decision": decision, "state": cooling}, config_path)
    check("the per-token cooldown blocks re-entry", not verdict.get("approved"))

    held = dict(state, open_positions=[{"token_address": fake_snapshot()["token_address"], "symbol": "TEST", "entry_usd": 50.0}])
    verdict = run_subagent("subagents.risk", {"snapshot": fake_snapshot(), "decision": decision, "state": held}, config_path)
    check("holding the token already blocks a second entry", not verdict.get("approved"))


def test_instructor(config_path: Path) -> None:
    print("\n-- instructor sub-agent --")
    state = {
        "cash_usd": 1_000.0,
        "equity_usd": 1_000.0,
        "exposure_usd": 0.0,
        "open_positions": [],
        "trades_today": 0,
        "realized_pnl_today": 0.0,
    }

    plan = run_subagent("subagents.instructor", {"state": state}, config_path)
    check("instructor returns a plan with sources", bool(plan.get("sources")), str(plan))
    check("sources are from the known set", set(plan.get("sources") or []) <= {"news", "forums", "feeds"})
    check("plan allows at least one entry", int(plan.get("max_candidates_per_cycle") or 0) >= 1, str(plan.get("max_candidates_per_cycle")))
    check("plan respects the config ceiling", int(plan.get("max_candidates") or 0) <= config_mod.DEFAULTS["scan"]["max_candidates"])
    check("plan carries a directive", bool(plan.get("directive")))

    broke = dict(state, realized_pnl_today=-10_000.0)
    plan = run_subagent("subagents.instructor", {"state": broke}, config_path)
    check("a spent loss budget pauses entries", int(plan.get("max_candidates_per_cycle") or 0) == 0, str(plan))
    check("the paused posture is named", plan.get("risk_posture") == "pause", str(plan.get("risk_posture")))

    full = dict(state, open_positions=[{"symbol": "A"}, {"symbol": "B"}, {"symbol": "C"}])
    plan = run_subagent("subagents.instructor", {"state": full}, config_path)
    check("no free slots means no entries planned", int(plan.get("max_candidates_per_cycle") or 0) == 0)


def test_filter(config_path: Path) -> None:
    print("\n-- filter sub-agent (offline) --")
    snapshot = fake_snapshot()
    leads = [
        {"source": "news", "title": "TEST is trending", "url": "", "tickers": ["TEST"], "mints": []},
        {"source": "forums", "title": "still buying TEST", "url": "", "tickers": ["TEST"], "mints": []},
        {"source": "forums", "title": "unrelated chatter", "url": "", "tickers": [], "mints": []},
    ]

    result = run_subagent(
        "subagents.filter",
        {"offline": True, "snapshots": [snapshot], "leads": leads, "max_candidates": 5},
        config_path,
    )
    candidates = result.get("candidates") or []
    check("a healthy token survives the filter", len(candidates) == 1, str(result)[:200])
    if candidates:
        check("mentions across sources are counted", candidates[0]["corroboration"] >= 2, str(candidates[0]["corroboration"]))
        check("candidate keeps its provenance", "news" in candidates[0]["sources"], str(candidates[0]["sources"]))

    thin = fake_snapshot(liquidity=500.0)
    result = run_subagent(
        "subagents.filter",
        {"offline": True, "snapshots": [thin], "leads": leads, "max_candidates": 5},
        config_path,
    )
    check("a thin token is rejected", not (result.get("candidates") or []))
    check("the rejection is explained", bool(result.get("skipped_sample")))

    result = run_subagent(
        "subagents.filter",
        {"offline": True, "snapshots": [snapshot], "leads": leads, "exclude_tokens": [snapshot["token_address"]]},
        config_path,
    )
    check("an already-held token is excluded", not (result.get("candidates") or []))


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="coinpilot-selftest-"))
    db_path = workdir / "paper.db"
    config_path = workdir / "config.json"
    config_path.write_text(json.dumps({"storage": {"db_path": str(db_path)}, "llm": {"enabled": False}}), encoding="utf-8")

    config = config_mod.default_config()
    config["storage"]["db_path"] = str(db_path)
    config["llm"]["enabled"] = False

    print(f"CoinPilot selftest (offline) in {workdir}")
    test_rules(config)
    test_broker(config, db_path)
    test_risk_agent(config_path)
    test_instructor(config_path)
    test_filter(config_path)

    print("")
    if FAILURES:
        print(f"{FAILURES} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
