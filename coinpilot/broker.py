"""Simulated fills: fees, slippage and a liquidity guard. No wallet, no keys."""

from __future__ import annotations

from . import store


class BrokerError(RuntimeError):
    """Raised when a paper order cannot be filled."""


def _fill(price: float, slippage_pct: float, side: str) -> float:
    slip = abs(slippage_pct) / 100.0
    return price * (1.0 + slip) if side == "buy" else price * (1.0 - slip)


def buy(connection, snap: dict, size_usd: float, config: dict, thesis: str = "") -> dict:
    price = float(snap.get("price_usd") or 0)
    if price <= 0:
        raise BrokerError("no price for token")

    size_usd = round(float(size_usd), 2)
    if size_usd <= 0:
        raise BrokerError("order size is zero")

    available = store.cash(connection)
    if size_usd > available + 1e-9:
        raise BrokerError(f"insufficient paper cash: have ${available:.2f}, need ${size_usd:.2f}")

    paper = config["paper"]
    fill_price = _fill(price, paper["slippage_pct"], "buy")
    fee_usd = size_usd * paper["fee_pct"] / 100.0
    qty = (size_usd - fee_usd) / fill_price
    if qty <= 0:
        raise BrokerError("size too small after fees")

    symbol = snap.get("symbol") or "?"
    position_id = store.insert_position(
        connection,
        token_address=snap["token_address"],
        symbol=symbol,
        pair_address=snap.get("pair_address"),
        qty=qty,
        entry_price=fill_price,
        entry_usd=size_usd,
        thesis=thesis,
    )
    store.add_trade(
        connection,
        token_address=snap["token_address"],
        symbol=symbol,
        side="buy",
        qty=qty,
        price=fill_price,
        usd=size_usd,
        fee_usd=fee_usd,
        reason=thesis,
    )

    state = store.get_state(connection)
    store.set_state(
        connection,
        {
            "cash_usd": f"{available - size_usd:.6f}",
            "trades_today": int(float(state.get("trades_today") or 0)) + 1,
        },
    )

    return {
        "position_id": position_id,
        "symbol": symbol,
        "side": "buy",
        "qty": qty,
        "fill_price": fill_price,
        "usd": size_usd,
        "fee_usd": fee_usd,
    }


def sell(connection, position: dict, snap: dict, config: dict, reason: str = "") -> dict:
    price = float(snap.get("price_usd") or 0)
    if price <= 0:
        raise BrokerError("no price for token")

    paper = config["paper"]
    fill_price = _fill(price, paper["slippage_pct"], "sell")
    gross = float(position["qty"]) * fill_price
    fee_usd = gross * paper["fee_pct"] / 100.0
    proceeds = gross - fee_usd
    pnl = proceeds - float(position["entry_usd"])

    store.close_position(connection, int(position["id"]), fill_price, proceeds, pnl)
    store.add_trade(
        connection,
        token_address=position["token_address"],
        symbol=position["symbol"],
        side="sell",
        qty=float(position["qty"]),
        price=fill_price,
        usd=proceeds,
        fee_usd=fee_usd,
        reason=reason,
        pnl_usd=pnl,
    )

    state = store.get_state(connection)
    available = float(state.get("cash_usd") or 0)
    realized_today = float(state.get("realized_pnl_today") or 0) + pnl
    realized_total = float(state.get("realized_pnl_total") or 0) + pnl
    store.set_state(
        connection,
        {
            "cash_usd": f"{available + proceeds:.6f}",
            "realized_pnl_today": f"{realized_today:.6f}",
            "realized_pnl_total": f"{realized_total:.6f}",
            "trades_today": int(float(state.get("trades_today") or 0)) + 1,
        },
    )

    return {
        "position_id": int(position["id"]),
        "symbol": position["symbol"],
        "side": "sell",
        "qty": float(position["qty"]),
        "fill_price": fill_price,
        "usd": proceeds,
        "fee_usd": fee_usd,
        "pnl_usd": pnl,
        "pnl_pct": (pnl / float(position["entry_usd"]) * 100.0) if float(position["entry_usd"]) else 0.0,
    }
