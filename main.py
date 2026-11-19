#!/usr/bin/env python3
"""CoinPilot: a tiny paper-trading agent for Solana meme coins.

The orchestrator makes no trading decisions itself. It spawns short-lived
sub-agent processes, hands each one a JSON payload on stdin and reads one JSON
result back. Nothing here touches a wallet: fills are simulated.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from coinpilot import config, market, store
from coinpilot.llm import LLM

SUBAGENT_TIMEOUT = 180
SOL_MINT = "So11111111111111111111111111111111111111112"
AGENT_SOURCES = ("news", "forums", "feeds")


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def money(value) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def price_text(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "?"
    if number <= 0:
        return "0"
    if number < 0.0001:
        return f"{number:.10f}"
    if number < 1:
        return f"{number:.6f}"
    return f"{number:,.4f}"


def spawn(module: str, payload: dict, timeout: int = SUBAGENT_TIMEOUT) -> dict:
    """Run one sub-agent process and return its JSON result."""

    root = config.PROJECT_ROOT
    environment = os.environ.copy()
    config_path = config.resolve_config_path()
    if config_path is not None:
        environment["COINPILOT_CONFIG"] = str(config_path)
    environment["PYTHONPATH"] = os.pathsep.join(f for f in (str(root), environment.get("PYTHONPATH")) if f)

    process = subprocess.run(
        [sys.executable, "-m", module],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(root),
        env=environment,
        timeout=timeout,
    )

    stdout = (process.stdout or "").strip()
    for line in (process.stderr or "").strip().splitlines():
        if line.strip():
            print(f"    {module.rsplit('.', 1)[-1]}: {line.strip()}", flush=True)

    if not stdout:
        stderr = (process.stderr or "").strip()[-400:]
        raise RuntimeError(f"{module} exited {process.returncode} with no output: {stderr}")

    try:
        result = json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{module} returned non-JSON: {stdout[-400:]}") from exc

    if isinstance(result, dict) and result.get("ok") is False:
        raise RuntimeError(f"{module}: {result.get('error')}")
    if not isinstance(result, dict):
        raise RuntimeError(f"{module} returned {type(result).__name__}, expected an object")
    return result


def spawn_many(tasks: list[tuple[str, dict]], timeout: int = SUBAGENT_TIMEOUT) -> list[dict]:
    """Run several sub-agents side by side. Each result is {ok, result|error}."""

    if not tasks:
        return []

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(len(tasks), 6)) as pool:
        futures = [pool.submit(spawn, module, payload, timeout) for module, payload in tasks]
        outcomes = []
        for future in futures:
            try:
                outcomes.append({"ok": True, "result": future.result()})
            except Exception as exc:  # noqa: BLE001 - one failed scout must not stop the others
                outcomes.append({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return outcomes


def portfolio_state(settings: dict, connection) -> dict:
    positions = store.open_positions(connection)
    marks: dict[str, dict] = {}
    if positions:
        try:
            marks = market.token_snapshots(settings["chain"], [item["token_address"] for item in positions])
        except Exception as exc:  # noqa: BLE001 - marks are advisory here
            log(f"mark lookup failed: {exc}")

    cash = store.cash(connection)
    exposure = 0.0
    market_value = 0.0
    for position in positions:
        mark = marks.get(position["token_address"]) or {}
        price = mark.get("price_usd") or position["entry_price"]
        exposure += float(position["entry_usd"])
        market_value += float(position["qty"]) * float(price)

    row = store.get_state(connection)
    cooldown_minutes = float(settings["risk"]["cooldown_minutes"])
    cooldowns = store.recent_token_activity(connection, time.time() - cooldown_minutes * 60)

    return {
        "cash_usd": cash,
        "equity_usd": cash + market_value,
        "exposure_usd": exposure,
        "unrealized_pnl_usd": market_value - exposure,
        "open_positions": [
            {
                "id": position["id"],
                "token_address": position["token_address"],
                "symbol": position["symbol"],
                "entry_usd": position["entry_usd"],
            }
            for position in positions
        ],
        "trades_today": int(float(row.get("trades_today") or 0)),
        "realized_pnl_today": float(row.get("realized_pnl_today") or 0.0),
        "cooldowns": cooldowns,
        "marks": marks,
        "now": time.time(),
    }


def manage_exits(settings: dict, connection) -> list[dict]:
    positions = store.open_positions(connection)
    if not positions:
        return []

    state = portfolio_state(settings, connection)
    marks = state["marks"]
    rules = settings["strategy"]
    exits: list[dict] = []

    for position in positions:
        snap = marks.get(position["token_address"])
        if not snap or not snap.get("price_usd"):
            log(f"{position['symbol']}: no fresh price, holding")
            continue

        price = float(snap["price_usd"])
        peak = max(float(position["peak_price"] or 0.0), price)
        if peak > float(position["peak_price"] or 0.0):
            store.update_peak(connection, int(position["id"]), peak)

        entry = float(position["entry_price"]) or price
        change_pct = (price / entry - 1.0) * 100.0
        peak_change_pct = (peak / entry - 1.0) * 100.0
        held_minutes = (time.time() - float(position["opened_at"])) / 60.0

        reason = None
        if change_pct <= float(rules["stop_loss_pct"]):
            reason = f"stop loss {change_pct:+.0f}%"
        elif change_pct >= float(rules["take_profit_pct"]):
            reason = f"take profit {change_pct:+.0f}%"
        else:
            trailing = float(rules.get("trailing_stop_pct") or 0.0)
            drawdown = ((peak - price) / peak * 100.0) if peak > 0 else 0.0
            if trailing > 0 and peak_change_pct > trailing and drawdown >= trailing:
                reason = f"trailing stop {drawdown:.0f}% off a +{peak_change_pct:.0f}% peak"
            elif held_minutes >= float(rules["max_hold_minutes"]):
                reason = f"time exit after {held_minutes:.0f}m"

        if not reason:
            log(f"{position['symbol']}: {change_pct:+.1f}% after {held_minutes:.0f}m, holding")
            continue

        try:
            result = spawn(
                "subagents.trader",
                {"action": "close", "position_id": position["id"], "snapshot": snap, "reason": reason},
                timeout=60,
            )
        except Exception as exc:  # noqa: BLE001
            log(f"{position['symbol']}: close failed: {exc}")
            continue

        if result.get("skipped"):
            log(f"{position['symbol']}: exit skipped ({result['skipped']})")
            continue

        pnl_pct = float(result.get("pnl_pct") or 0.0)
        log(f"CLOSED {result.get('symbol')} - {reason}: {money(result.get('pnl_usd'))} ({pnl_pct:+.1f}%)")
        exits.append(result)

    return exits


def fallback_plan(settings: dict, state: dict) -> dict:
    """Used only when the instructor sub-agent cannot answer."""

    scan = settings["scan"]
    slots = int(settings["strategy"]["max_positions"]) - len(state.get("open_positions") or [])
    return {
        "directive": "Fallback plan: standard filters, standard posture.",
        "sources": [source for source in (scan.get("sources") or []) if source in AGENT_SOURCES],
        "max_candidates": int(scan.get("max_candidates") or 8),
        "max_candidates_per_cycle": int(settings["llm"].get("max_candidates_per_cycle") or 3) if slots > 0 else 0,
        "risk_posture": "normal",
        "notes": ["fallback plan: the instructor sub-agent did not answer"],
        "source_of_truth": "fallback",
    }


def discovery(settings: dict, state: dict, exclude: list[str], verbose: bool = False) -> dict:
    """instructor -> one scout per source, in parallel -> filter. Writes nothing."""

    plan = fallback_plan(settings, state)
    try:
        plan = spawn("subagents.instructor", {"state": state}, timeout=90)
    except Exception as exc:  # noqa: BLE001
        log(f"instructor failed, using the fallback plan: {exc}")

    sources = [source for source in (plan.get("sources") or []) if source in AGENT_SOURCES]
    log(f"instructor [{plan.get('source_of_truth')}] {plan.get('risk_posture')}: {plan.get('directive')}")
    for note in plan.get("notes") or []:
        log(f"  note: {note}")

    tasks = [
        ("subagents.scout", {"source": source, "directive": plan.get("directive") or "", "exclude_tokens": exclude})
        for source in sources
    ]

    leads: list[dict] = []
    for source, outcome in zip(sources, spawn_many(tasks, timeout=120)):
        if not outcome.get("ok"):
            log(f"scout[{source}] failed: {outcome.get('error')}")
            continue
        result = outcome.get("result") or {}
        found = result.get("leads") or []
        log(f"scout[{source}]: {len(found)} lead(s) from {result.get('scanned', 0)} item(s)")
        if verbose:
            for item in found[:5]:
                label = item.get("tickers") or item.get("mints") or ["?"]
                print(f"    {item.get('source')}: {str(item.get('title'))[:90]} {label}")
        leads.extend(found)

    if not leads:
        return {"plan": plan, "leads": [], "candidates": [], "filtered": {}}

    try:
        filtered = spawn(
            "subagents.filter",
            {
                "leads": leads,
                "directive": plan.get("directive") or "",
                "exclude_tokens": exclude,
                "max_candidates": plan.get("max_candidates"),
            },
            timeout=150,
        )
    except Exception as exc:  # noqa: BLE001
        log(f"filter failed: {exc}")
        return {"plan": plan, "leads": leads, "candidates": [], "filtered": {}}

    candidates = filtered.get("candidates") or []
    log(
        f"filter: {len(candidates)} candidate(s) from {filtered.get('priced', 0)} resolved out of "
        f"{filtered.get('leads_in', len(leads))} lead(s), {filtered.get('skipped_count', 0)} rejected"
    )
    return {"plan": plan, "leads": leads, "candidates": candidates, "filtered": filtered}


def find_entries(settings: dict, connection) -> list[dict]:
    rules = settings["strategy"]
    positions = store.open_positions(connection)
    slots = int(rules["max_positions"]) - len(positions)
    if slots <= 0:
        log("all position slots in use")
        return []

    exclude = [position["token_address"] for position in positions]
    state = portfolio_state(settings, connection)
    outcome = discovery(settings, state, exclude)
    plan = outcome["plan"]
    candidates = outcome["candidates"]
    store.record_candidates(connection, candidates)

    attempts = int(plan.get("max_candidates_per_cycle") or 0)
    if attempts <= 0:
        log(f"instructor: no entries this cycle ({plan.get('risk_posture')})")
        return []
    if not candidates:
        return []

    opened: list[dict] = []

    for candidate in candidates[:attempts]:
        if slots <= 0:
            break
        snap = candidate.get("snapshot") or {}
        symbol = snap.get("symbol") or "?"
        token_address = snap.get("token_address")

        try:
            decision = spawn("subagents.analyst", {"snapshot": snap, "rule": candidate.get("rule")}, timeout=SUBAGENT_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            log(f"{symbol}: analyst failed: {exc}")
            continue

        store.record_decision(connection, "analyst", decision, token_address, symbol)
        score = float(decision.get("score") or 0.0)
        thesis = (decision.get("thesis") or "").strip()
        log(f"{symbol}: {decision.get('action')} score {score:.0f} via {decision.get('source')} - {thesis[:120]}")
        store.update_candidate(
            connection, token_address, "skip" if decision.get("action") != "buy" else "judged", thesis
        )

        state = portfolio_state(settings, connection)
        try:
            verdict = spawn("subagents.risk", {"snapshot": snap, "decision": decision, "state": state}, timeout=60)
        except Exception as exc:  # noqa: BLE001
            log(f"{symbol}: risk check failed: {exc}")
            continue

        store.record_decision(connection, "risk", verdict, token_address, symbol)
        if not verdict.get("approved"):
            log(f"{symbol}: blocked - {verdict.get('reason')}")
            store.update_candidate(connection, token_address, "blocked", str(verdict.get("reason") or ""))
            continue

        try:
            result = spawn(
                "subagents.trader",
                {
                    "action": "open",
                    "snapshot": snap,
                    "size_usd": verdict.get("size_usd"),
                    "reason": thesis[:400],
                },
                timeout=60,
            )
        except Exception as exc:  # noqa: BLE001
            log(f"{symbol}: open failed: {exc}")
            continue

        if result.get("skipped"):
            log(f"{symbol}: open skipped ({result['skipped']})")
            continue

        log(f"OPENED {result.get('symbol')} {money(verdict.get('size_usd'))} @ {price_text(result.get('fill_price'))}")
        store.update_candidate(connection, token_address, "entered", thesis)
        opened.append(result)
        slots -= 1

    return opened


def cycle(settings: dict, connection) -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    store.bootstrap(connection, float(settings["risk"]["starting_cash_usd"]), today)
    store.rollover_day(connection, today)

    killed = config.kill_switch_path(settings).exists()

    closed = manage_exits(settings, connection)
    if killed:
        log("kill switch file present: exits only, no new entries")
        opened: list[dict] = []
    else:
        opened = find_entries(settings, connection)

    state = portfolio_state(settings, connection)
    log(
        f"equity {money(state['equity_usd'])} "
        f"(cash {money(state['cash_usd'])}, unrealized {money(state['unrealized_pnl_usd'])})"
    )
    return {"closed": closed, "opened": opened}


def command_run(settings: dict, cycles: int, interval: int) -> int:
    connection = store.connect(config.db_path(settings))
    wait = interval or int(settings["loop"].get("interval_seconds") or 300)
    ceiling = cycles or int(settings["loop"].get("max_cycles") or 0)
    done = 0
    try:
        while True:
            done += 1
            log(f"cycle {done}")
            try:
                cycle(settings, connection)
            except Exception as exc:  # noqa: BLE001 - the loop outlives a bad cycle
                log(f"cycle failed: {type(exc).__name__}: {exc}")
            if ceiling and done >= ceiling:
                break
            log(f"sleeping {wait}s")
            time.sleep(max(15, wait))
    except KeyboardInterrupt:
        log("stopped")
    finally:
        connection.close()
    return 0


def command_status(settings: dict) -> int:
    connection = store.connect(config.db_path(settings))
    try:
        stats = store.totals(connection)
        positions = store.open_positions(connection)
        state = portfolio_state(settings, connection)

        print(f"mode: paper ({settings['chain']})")
        print(f"config: {config.resolve_config_path()}")
        print(f"database: {config.db_path(settings)}")
        print(f"cash: {money(stats['cash_usd'])}   equity: {money(state['equity_usd'])}")
        print(f"started with {money(stats['starting_cash_usd'])} -> equity {money(state['equity_usd'])}")
        print(
            f"realized: today {money(stats['realized_pnl_today'])} / all time {money(stats['realized_pnl_total'])}   "
            f"closed round trips: {stats['closed_trades']}   trades today: {state['trades_today']}"
        )

        if positions:
            print("\nopen positions:")
            marks = state["marks"]
            for position in positions:
                mark = marks.get(position["token_address"]) or {}
                price = mark.get("price_usd") or position["entry_price"]
                value = float(position["qty"]) * float(price)
                pnl = value - float(position["entry_usd"])
                pct = (pnl / float(position["entry_usd"]) * 100.0) if float(position["entry_usd"]) else 0.0
                print(
                    f"  #{position['id']} {position['symbol']:<10} {money(position['entry_usd'])} "
                    f"@ {price_text(position['entry_price'])} -> {price_text(price)}  {money(pnl)} ({pct:+.1f}%)"
                )
        else:
            print("\nopen positions: none")

        trades = store.recent_trades(connection, 10)
        if trades:
            print("\nrecent trades:")
            for trade in trades:
                when = datetime.fromtimestamp(trade["ts"], timezone.utc).strftime("%m-%d %H:%M")
                pnl = "" if trade.get("pnl_usd") is None else f"  pnl {trade['pnl_usd']:+.2f}"
                print(
                    f"  {when} {trade['side'].upper():<4} {trade['symbol']:<10} {money(trade['usd'])} "
                    f"@ {price_text(trade['price'])}{pnl}  {trade.get('reason') or ''}"
                )
        return 0
    finally:
        connection.close()


def command_scan(settings: dict) -> int:
    connection = store.connect(config.db_path(settings))
    try:
        state = portfolio_state(settings, connection)
        outcome = discovery(settings, state, [], verbose=True)
        stored = store.record_candidates(connection, outcome["candidates"])
    finally:
        connection.close()

    plan = outcome["plan"]
    candidates = outcome["candidates"]
    filtered = outcome["filtered"] or {}

    print(f"\nplan [{plan.get('source_of_truth')}] {plan.get('risk_posture')}: {plan.get('directive')}")
    print(
        f"sources: {', '.join(plan.get('sources') or []) or 'none'}   "
        f"keep up to {plan.get('max_candidates')}   attempt {plan.get('max_candidates_per_cycle')} entry(s)\n"
    )
    print(
        f"{len(outcome['leads'])} lead(s) -> {len(candidates)} candidate(s); "
        f"{filtered.get('priced', 0)} resolved, {filtered.get('skipped_count', 0)} rejected\n"
    )

    for item in candidates:
        snap = item.get("snapshot") or {}
        rule = item.get("rule") or {}
        age = snap.get("age_minutes") or 0
        change = snap.get("change_h24") or 0
        print(
            f"  {float(rule.get('score') or 0):5.1f}  {str(snap.get('symbol') or '?'):<10} "
            f"{money(snap.get('liquidity_usd')):>12} liq  {money(snap.get('volume_h24')):>14} vol24  "
            f"{age:6.0f}m  {change:+7.0f}% 24h  {item.get('corroboration')}x  "
            f"{','.join(item.get('sources') or [])}"
        )
        for signal in (rule.get("signals") or [])[:4]:
            print(f"         - {signal}")

    rejected = filtered.get("skipped_sample") or []
    if rejected:
        print("\nrejected:")
        for row in rejected:
            print(f"  {str(row.get('symbol') or '?'):<10} {row.get('reason')}")
    return 0


def command_doctor(settings: dict) -> int:
    problems = 0
    config_path = config.resolve_config_path()
    print(f"config file: {config_path if config_path and config_path.exists() else 'MISSING'}")
    if config_path is None or not config_path.exists():
        problems += 1

    client = LLM(settings)
    if client.available:
        print(f"model endpoint: {client.base_url} model {client.model}")
        print(f"api key: {'set' if client.key else 'MISSING (bot falls back to the rule engine)'}")
    else:
        print("model endpoint: disabled, rule engine only")

    try:
        snap = market.token_snapshot(settings["chain"], SOL_MINT)
        if snap and snap.get("price_usd"):
            print(f"dexscreener: ok (SOL {money(snap['price_usd'])})")
        else:
            print("dexscreener: reachable but returned no SOL pair")
            problems += 1
    except Exception as exc:  # noqa: BLE001
        print(f"dexscreener: FAILED {exc}")
        problems += 1

    try:
        connection = store.connect(config.db_path(settings))
        connection.close()
        print(f"database: ok ({config.db_path(settings)})")
    except Exception as exc:  # noqa: BLE001
        print(f"database: FAILED {exc}")
        problems += 1

    kill_file = config.kill_switch_path(settings)
    if kill_file.exists():
        print(f"kill switch: ACTIVE ({kill_file.name}) - exits only, no new entries")
    else:
        print("kill switch: not set")

    return 1 if problems else 0


def command_reset(settings: dict, confirmed: bool) -> int:
    path = config.db_path(settings)
    if not confirmed:
        print(f"refusing to delete {path} without --yes")
        return 2
    for suffix in ("", "-wal", "-shm"):
        target = Path(f"{path}{suffix}")
        if target.exists():
            target.unlink()
            print(f"removed {target}")
    connect_time = datetime.now(timezone.utc)
    connection = store.connect(path)
    try:
        store.bootstrap(connection, float(settings["risk"]["starting_cash_usd"]), connect_time.strftime("%Y-%m-%d"))
    finally:
        connection.close()
    print(f"fresh paper account with {money(settings['risk']['starting_cash_usd'])}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coinpilot", description="Tiny paper-trading agent for Solana meme coins.")
    parser.add_argument("--config", help="config file (default: config.json, falling back to config.example.json)")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="loop cycles until stopped")
    run_parser.add_argument("--cycles", type=int, default=0, help="stop after N cycles (0 = run forever)")
    run_parser.add_argument("--interval", type=int, default=0, help="seconds between cycles")

    sub.add_parser("once", help="run a single cycle")
    sub.add_parser("status", help="show the paper portfolio")
    sub.add_parser("scan", help="print scored candidates without trading")
    sub.add_parser("doctor", help="check config, API reachability and the database")

    reset_parser = sub.add_parser("reset", help="delete the paper database")
    reset_parser.add_argument("--yes", action="store_true", help="confirm the deletion")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.config:
        # Sub-agents run as separate processes and read the config from this variable.
        os.environ["COINPILOT_CONFIG"] = arguments.config
    settings = config.load_config(arguments.config)

    if str(settings.get("mode") or "paper") != "paper":
        log(f"mode '{settings.get('mode')}' is not supported: CoinPilot is paper-only and will simulate fills")

    if arguments.command == "run":
        return command_run(settings, arguments.cycles, arguments.interval)
    if arguments.command == "once":
        connection = store.connect(config.db_path(settings))
        try:
            cycle(settings, connection)
        finally:
            connection.close()
        return 0
    if arguments.command == "status":
        return command_status(settings)
    if arguments.command == "scan":
        return command_scan(settings)
    if arguments.command == "doctor":
        return command_doctor(settings)
    if arguments.command == "reset":
        return command_reset(settings, arguments.yes)

    print(f"unknown command {arguments.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
