"""Capacity-constrained research replay, separate from Freqtrade's native matching.

Decisions use completed hourly/daily data. Submitted market quantities may fill
partially over the NEXT five-minute interval at its close plus adverse slippage.
Exit remainders persist. Entry remainders expire after that interval. This is an
explicit execution proxy, not evidence of order-book liquidity or exchange fills.
"""

import math
from copy import deepcopy

import numpy as np
import pandas as pd


def new_state(initial):
    return {
        "cash": float(initial),
        "positions": {},
        "pending_exits": {},
        "cooldown": {},
        "sequence": 0,
        "peak": float(initial),
        "halted": False,
        "requested_notional": 0.0,
        "filled_entry_notional": 0.0,
        "entry_requests": 0,
        "partial_entries": 0,
        "unfilled_entries": 0,
        "partial_exits": 0,
        "max_exit_delay_minutes": 0.0,
    }


def marked_equity(state, prices, fee, slip):
    return state["cash"] + math.fsum(
        p["side"] * p["quantity"] * (prices[s] - p["entry_price"])
        - p["quantity"] * prices[s] * (fee + slip)
        for s, p in state["positions"].items()
    )


def replay(data, plan, candidate, initial, extra_bps, start, end, state=None):  # noqa: C901
    # Keep the funding, decision, fill and valuation transaction order explicit.
    state = deepcopy(state) if state is not None else new_state(initial)
    symbols = sorted(plan["symbols"])
    start, end = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    clocks = pd.date_range(start, end, inclusive="left", freq="5min")
    fee, slip = plan["fee_per_side"], extra_bps / 10000
    rules, risk = plan["execution"], plan["risk"]
    arrays, hourly, funding, previous = {}, {}, {}, {}
    for s in symbols:
        frame = data[s]["bars"]
        if not frame.loc[clocks].index.equals(clocks):
            raise ValueError("Incomplete execution interval")
        arrays[s] = frame.loc[clocks, ["open", "high", "low", "close", "quote_volume"]].to_numpy()
        hourly[s] = data[s]["features"].to_dict("index")
        funding[s] = data[s]["funding"].to_dict("index")
        previous[s] = frame.loc[start - pd.Timedelta(minutes=5)].to_dict()
    prices = {s: float(previous[s]["close"]) for s in symbols}
    initial_equity = marked_equity(state, prices, fee, slip)
    curve, events, decisions, trades, checkpoints = [], [], [], [], []
    curve.append(
        {
            "date": start,
            "equity": initial_equity,
            "cash": state["cash"],
            "gross_exposure": sum(p["quantity"] * prices[s] for s, p in state["positions"].items()),
        }
    )
    for i, clock in enumerate(clocks):
        # Boundary inventory is settled from the preceding bar before funding is applied.
        for s, p in list(state["positions"].items()):
            event = funding[s].get(clock)
            if event is not None:
                amount = -p["side"] * p["quantity"] * event["mark"] * event["funding_rate"]
                state["cash"] += amount
                p["net_pnl"] += amount
                events.append(
                    {
                        "type": "funding",
                        "time": clock.isoformat(),
                        "symbol": s,
                        "position_id": p["id"],
                        "cash_delta": amount,
                        "quantity": p["quantity"],
                        "side": p["side"],
                        **event,
                    }
                )
        value = marked_equity(state, prices, fee, slip)
        if value <= 0:
            raise ValueError("Account insolvent; no valid continuation")
        state["peak"] = max(state["peak"], value)
        if value < state["peak"] * (1 - risk["account_drawdown_halt"]):
            state["halted"] = True
        for s, p in state["positions"].items():
            old = previous[s]
            stop = old["low"] <= p["stop"] if p["side"] == 1 else old["high"] >= p["stop"]
            # Only a bar fully held before its close may trigger the delayed stop.
            stop = stop and pd.Timestamp(p["opened_at"]) <= clock - pd.Timedelta(minutes=5)
            reason = "account_drawdown" if state["halted"] else "fixed_atr_stop" if stop else None
            if reason:
                state["pending_exits"].setdefault(
                    s, {"reason": reason, "submitted_at": clock.isoformat()}
                )
        requests = {}
        if clock.minute == 0:
            rows = {s: hourly[s][clock] for s in symbols}
            for s, p in state["positions"].items():
                f = rows[s]
                exit_signal = (
                    (
                        f["previous_close"] < f["low20"]
                        if p["side"] == 1
                        else f["previous_close"] > f["high20"]
                    )
                    if candidate == "Donchian55"
                    else np.sign(f["momentum"]) != p["side"]
                    if candidate == "Momentum63"
                    else False
                )
                reason = (
                    "liquidity_exit"
                    if not f["eligible"]
                    else "signal_exit"
                    if exit_signal
                    else None
                )
                if reason:
                    state["pending_exits"].setdefault(
                        s, {"reason": reason, "submitted_at": clock.isoformat()}
                    )
            # Fixed alphabetical admission prevents hindsight ordering by subsequent returns.
            for s in symbols:
                f = rows[s]
                if (
                    state["halted"]
                    or s in state["positions"]
                    or not f["eligible"]
                    or len(state["positions"]) + len(requests) >= risk["maximum_positions"]
                    or clock.value < state["cooldown"].get(s, 0)
                ):
                    continue
                required = [
                    f[k]
                    for k in (
                        "atr",
                        "previous_close",
                        "prior_hour_quote",
                        "momentum",
                        "high55",
                        "low55",
                    )
                ]
                if not np.isfinite(required).all() or f["atr"] <= 0 or f["prior_hour_quote"] <= 0:
                    continue
                if candidate == "Donchian55":
                    side = (
                        1
                        if f["previous_close"] > f["high55"]
                        and f["before_previous_close"] <= f["high55"]
                        else -1
                        if f["previous_close"] < f["low55"]
                        and f["before_previous_close"] >= f["low55"]
                        else 0
                    )
                elif candidate == "Momentum63":
                    side = int(np.sign(f["momentum"]))
                elif candidate == "LongOnlyControl":
                    side = 1
                else:
                    raise ValueError("Unregistered mechanism")
                if not side:
                    continue
                price = f["previous_close"]
                gross = sum(p["quantity"] * prices[k] for k, p in state["positions"].items())
                # Reserve all simultaneous requests before admitting the next symbol.
                gross += sum(r["quantity"] * r["reference_price"] for r in requests.values())
                budget = min(
                    value * risk["maximum_position_fraction"],
                    max(0, value * risk["maximum_gross_fraction"] - gross),
                    value * risk["risk_per_position"] * price / (2 * f["atr"]),
                )
                requested_qty = budget / price
                causal_cap = rules["prior_hour_participation"] * f["prior_hour_quote"] / 12 / price
                qty = min(requested_qty, causal_cap)
                if qty <= 0:
                    continue
                requests[s] = {
                    "quantity": qty,
                    "side": side,
                    "atr": f["atr"],
                    "reference_price": price,
                    "submitted_at": clock.isoformat(),
                }
                state["entry_requests"] += 1
                state["requested_notional"] += requested_qty * price
                decisions.append(
                    {
                        "time": clock.isoformat(),
                        "symbol": s,
                        "side": side,
                        "requested_quantity": requested_qty,
                        "submitted_quantity": qty,
                        "reference_price": price,
                        "prior_hour_quote": f["prior_hour_quote"],
                    }
                )
        fill_time = clock + pd.Timedelta(minutes=5)
        # Exit requests have priority; their unfilled quantities remain in the account.
        for s in sorted(state["pending_exits"]):
            if s not in state["positions"]:
                raise ValueError("Exit inventory missing")
            p, pending = state["positions"][s], state["pending_exits"][s]
            bar = arrays[s][i]
            price = bar[3] * (1 - p["side"] * slip)
            qty = min(p["quantity"], rules["realized_interval_participation"] * bar[4] / price)
            if qty <= 0:
                continue
            cash_delta = p["side"] * qty * (price - p["entry_price"]) - qty * price * fee
            state["cash"] += cash_delta
            p["net_pnl"] += cash_delta
            p["quantity"] -= qty
            events.append(
                {
                    "type": "exit",
                    "time": fill_time.isoformat(),
                    "submitted_at": pending["submitted_at"],
                    "symbol": s,
                    "position_id": p["id"],
                    "side": p["side"],
                    "quantity": qty,
                    "price": price,
                    "fee": qty * price * fee,
                    "cash_delta": cash_delta,
                    "interval_quote_volume": bar[4],
                    "reason": pending["reason"],
                }
            )
            delay = (fill_time - pd.Timestamp(pending["submitted_at"])).total_seconds() / 60
            state["max_exit_delay_minutes"] = max(state["max_exit_delay_minutes"], delay)
            if p["quantity"] < 1e-12:
                trades.append(
                    {**p, "closed_at": fill_time.isoformat(), "exit_reason": pending["reason"]}
                )
                del state["positions"][s]
                del state["pending_exits"][s]
                state["cooldown"][s] = (fill_time + pd.Timedelta(hours=24)).value
            else:
                state["partial_exits"] += 1
        for s, r in requests.items():
            bar = arrays[s][i]
            price = bar[3] * (1 + r["side"] * slip)
            qty = min(r["quantity"], rules["realized_interval_participation"] * bar[4] / price)
            if qty <= 0:
                state["unfilled_entries"] += 1
                continue
            if qty < r["quantity"] - 1e-12:
                state["partial_entries"] += 1
            state["sequence"] += 1
            charge = qty * price * fee
            state["cash"] -= charge
            p = {
                "id": state["sequence"],
                "side": r["side"],
                "quantity": qty,
                "initial_quantity": qty,
                "entry_price": price,
                "opened_at": fill_time.isoformat(),
                "stop": price - r["side"] * 2 * r["atr"],
                "net_pnl": -charge,
            }
            state["positions"][s] = p
            state["filled_entry_notional"] += qty * price
            events.append(
                {
                    "type": "entry",
                    "time": fill_time.isoformat(),
                    "submitted_at": r["submitted_at"],
                    "symbol": s,
                    "position_id": p["id"],
                    "side": r["side"],
                    "quantity": qty,
                    "price": price,
                    "fee": charge,
                    "cash_delta": -charge,
                    "interval_quote_volume": bar[4],
                }
            )
        prices = {s: arrays[s][i][3] for s in symbols}
        previous = {
            s: dict(
                zip(("open", "high", "low", "close", "quote_volume"), arrays[s][i], strict=False)
            )
            for s in symbols
        }
        value = marked_equity(state, prices, fee, slip)
        state["peak"] = max(state["peak"], value)
        curve.append(
            {
                "date": fill_time,
                "equity": value,
                "cash": state["cash"],
                "gross_exposure": sum(
                    p["quantity"] * prices[s] for s, p in state["positions"].items()
                ),
            }
        )
        if fill_time.day == 1 and fill_time.hour == 0 and fill_time.minute == 0:
            checkpoints.append({"time": fill_time.isoformat(), "state": deepcopy(state)})
    return {
        "state": state,
        "events": events,
        "decisions": decisions,
        "trades": trades,
        "curve": pd.DataFrame(curve),
        "checkpoints": checkpoints,
    }


def metrics(account, initial):
    curve = account["curve"].set_index("date").equity
    daily = curve.loc[(curve.index.hour == 0) & (curve.index.minute == 0)]
    returns = daily.pct_change(fill_method=None).dropna()
    deviation = float(returns.std(ddof=1))
    trades = account["trades"]
    positive = math.fsum(max(t["net_pnl"], 0) for t in trades)
    negative = -math.fsum(min(t["net_pnl"], 0) for t in trades)
    state = account["state"]
    by_pair = {}
    for s in {e["symbol"] for e in account["events"]}:
        by_pair[s] = math.fsum(e["cash_delta"] for e in account["events"] if e["symbol"] == s)
    return {
        "net_return": float(curve.iloc[-1] / initial - 1),
        "net_profit_usdt": float(curve.iloc[-1] - initial),
        "ending_equity": float(curve.iloc[-1]),
        "closed_trades": len(trades),
        "win_rate": sum(t["net_pnl"] > 0 for t in trades) / len(trades) if trades else None,
        "profit_factor": positive / negative if negative else None,
        "daily_sharpe": float(returns.mean() / deviation * math.sqrt(365))
        if deviation > 1e-15
        else None,
        "max_drawdown": float((1 - curve / curve.cummax()).max()),
        "fees_usdt": math.fsum(e.get("fee", 0) for e in account["events"]),
        "funding_net_usdt": math.fsum(
            e["cash_delta"] for e in account["events"] if e["type"] == "funding"
        ),
        "closed_net_pnl": math.fsum(t["net_pnl"] for t in trades),
        "open_positions": len(state["positions"]),
        "open_lifecycle_cash_pnl": math.fsum(p["net_pnl"] for p in state["positions"].values()),
        "unrealized_pnl_less_exit_reserve": float(curve.iloc[-1] - state["cash"]),
        "cash_pnl_by_symbol": by_pair,
        "account_halted": state["halted"],
        "entry_requests": state["entry_requests"],
        "partial_entries": state["partial_entries"],
        "unfilled_entries": state["unfilled_entries"],
        "partial_exits": state["partial_exits"],
        "requested_notional": state["requested_notional"],
        "filled_entry_notional": state["filled_entry_notional"],
        "max_exit_delay_minutes": state["max_exit_delay_minutes"],
        "maximum_gross_to_equity": float(
            (account["curve"].gross_exposure.to_numpy() / curve.to_numpy()).max()
        ),
    }
