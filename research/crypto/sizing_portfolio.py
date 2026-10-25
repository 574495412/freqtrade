"""Source forecast-buffer extension of the frozen resized-account engine.

Only source-default nearest-edge buffering is added; unbuffered mechanics are
unchanged, and constraints/stops/halts take priority. No exchange interface.
"""

import math
from copy import deepcopy

import numpy as np
import pandas as pd


def target_weights(row, symbols, gross, maximum):
    target = {s: float(row["target_" + s]) for s in symbols}
    if (
        not all(math.isfinite(v) for v in target.values())
        or sum(abs(v) for v in target.values()) > 1 + 1e-10
        or sum(v != 0 for v in target.values()) > maximum
        or not 0 <= gross <= 1
    ):
        raise ValueError("Invalid unit-gross target weights or account budget")
    return {s: gross * v for s, v in target.items() if v != 0}


def apply_forecast_buffer(target, row, positions, previous, equity, gross_limit):
    out = {}
    for symbol in previous:
        desired = target.get(symbol, 0.0)
        width = float(row.get("buffer_" + symbol, 0.0)) * gross_limit
        if not math.isfinite(width) or width < 0:
            raise ValueError("Buffer width must be finite and nonnegative")
        held = positions.get(symbol)
        current = held["side"] * held["q"] * previous[symbol] / equity if held else 0.0
        lower, upper = desired - width, desired + width
        out[symbol] = min(max(current, lower), upper)
    total = sum(abs(v) for v in out.values())
    if total > gross_limit and total > 0:
        out = {s: w * gross_limit / total for s, w in out.items()}
    return {s: w for s, w in out.items() if w != 0}


def replay(bars, funding, scores, config, start, end, initial_state=None):  # noqa: C901
    capital = float(config["capital"])
    fee = config["fee_bps"] / 10000
    slip = config["slippage_bps"] / 10000
    participation = config["participation"]
    symbols = sorted(bars)
    times = pd.date_range(start, end, freq="5min", inclusive="left", tz="UTC")
    data = {
        s: bars[s].loc[times, ["open", "high", "low", "close", "quote_volume"]].to_numpy()
        for s in symbols
    }
    events_by_s = {s: funding[s].to_dict("index") for s in symbols}
    previous = {s: float(bars[s].loc[times[0] - pd.Timedelta(minutes=5), "close"]) for s in symbols}
    index = {t: row.to_dict() for t, row in scores.iterrows()}
    state = deepcopy(initial_state) if initial_state else {}
    if state and state["next_clock"] != times[0].isoformat():
        raise ValueError("Resume clock must match the prior account boundary")
    if state and (state["config"] != config or state["symbols"] != symbols):
        raise ValueError("Resume account configuration or market universe differs")
    cash = state.get("cash", capital)
    positions = state.get("positions", {})
    pending = state.get("pending_exits", {})
    sequence = state.get("sequence", 0)
    halted = state.get("risk_halted", False)
    peak = state.get("peak", capital)
    last_risk_scale = state.get("last_risk_scale", 1.0)
    if state and any(abs(previous[s] - state["previous_prices"][s]) > 1e-10 for s in symbols):
        raise ValueError("Resume marks differ from the preceding actual market bar")
    ledger = []
    trades = []
    requests = []
    curve = []
    counters = state.get("counters", {})
    entry_requests = counters.get("entry_requests", 0)
    partial = counters.get("partial", 0)
    exit_partial = counters.get("exit_partial", 0)
    wanted = counters.get("wanted", 0.0)
    filled = counters.get("filled", 0.0)
    max_delay = counters.get("max_delay", 0.0)

    def value(prices):
        return cash + math.fsum(
            p["side"] * p["q"] * (prices[s] - p["price"]) - p["q"] * prices[s] * (fee + slip)
            for s, p in positions.items()
        )

    curve.append(
        {
            "date": times[0],
            "equity": value(previous),
            "cash": cash,
            "gross_exposure": sum(p["q"] * previous[s] for s, p in positions.items()),
            "net_exposure": sum(p["side"] * p["q"] * previous[s] for s, p in positions.items()),
        }
    )
    for i, clock in enumerate(times):
        for s, p in list(positions.items()):
            f = events_by_s[s].get(clock)
            if f is not None:
                delta = -p["side"] * p["q"] * f["mark"] * f["funding_rate"]
                cash += delta
                p["net_pnl"] += delta
                ledger.append(
                    {
                        "type": "funding",
                        "time": clock.isoformat(),
                        "symbol": s,
                        "position_id": p["id"],
                        "quantity": p["q"],
                        "side": p["side"],
                        "mark": f["mark"],
                        "rate": f["funding_rate"],
                        "cash_delta": delta,
                    }
                )
        equity = value(previous)
        peak = max(peak, equity)
        if equity <= 0:
            raise ValueError("Insolvent research account")
        if equity < peak * (1 - config["drawdown_halt"]):
            halted = True
        entries = {}
        for s, p in positions.items():
            # Observe stops after the preceding interval; exclude the entry bar.
            b = bars[s].loc[clock - pd.Timedelta(minutes=5)]
            breached = (
                b.low <= p["stop"] if p["side"] == 1 else b.high >= p["stop"]
            ) and pd.Timestamp(p["opened_at"]) < clock
            if halted or breached:
                request = pending.get(s)
                first = (
                    request["time"]
                    if request and request["reason"] in ("stop", "risk_halt")
                    else clock.isoformat()
                )
                pending[s] = {
                    "q": p["q"],
                    "time": first,
                    "reason": "risk_halt" if halted else "stop",
                }
        hourly = clock.minute == 0
        row = index.get(clock) if hourly else None
        if hourly and row is None:
            raise ValueError("Missing timestamped target portfolio")
        risk_scale = float(row["risk_scale"]) if hourly else last_risk_scale
        if not math.isfinite(risk_scale) or not 0 <= risk_scale <= 1:
            raise ValueError("Risk scale must be finite and between zero and one")
        scheduled = hourly and clock.hour % config["rebalance_hours"] == 0
        if scheduled or (hourly and risk_scale != last_risk_scale):
            target = target_weights(row, symbols, config["gross"], config["max_positions"])
            target = {s: w * risk_scale for s, w in target.items()} if not halted else {}
            gross_limit = config["gross"] * risk_scale
            if config.get("position_buffer") and not halted and risk_scale == last_risk_scale:
                target = apply_forecast_buffer(
                    target, row, positions, previous, equity, gross_limit
                )
            # Intent requests may budget planned releases; entry fills below
            # remain conditional on confirmed exits and actual risk capacity.
            gross = sum(
                min(p["q"], abs(target.get(s, 0)) * equity / previous[s]) * previous[s]
                for s, p in positions.items()
                if np.sign(target.get(s, 0)) == p["side"]
            )
            retained_names = sum(
                np.sign(target.get(s, 0)) == p["side"] and target.get(s, 0) != 0
                for s, p in positions.items()
            )
            reserved = 0.0
            for s in symbols:
                p = positions.get(s)
                w = target.get(s, 0.0)
                desired = abs(w) * equity / previous[s]
                if p:
                    if s in pending and pending[s]["reason"] in ("stop", "risk_halt"):
                        continue
                    retained = min(p["q"], desired) if np.sign(w) == p["side"] else 0.0
                    if p["q"] > retained + 1e-12:
                        pending[s] = {
                            "q": p["q"] - retained,
                            "time": clock.isoformat(),
                            "reason": "rebalance",
                        }
                        continue
                    # A newly computed intent replaces an unfilled rebalance exit,
                    # while stop/halt exits remain mandatory and cannot be canceled.
                    pending.pop(s, None)
                    desired = max(0.0, desired - p["q"])
                new_count = sum(name not in positions for name in entries)
                if not w or (p is None and retained_names + new_count >= config["max_positions"]):
                    continue
                budget = min(desired * previous[s], max(0, equity * gross_limit - gross - reserved))
                if budget <= max(1e-8, equity * 1e-12):
                    continue
                history = bars[s].loc[
                    (bars[s].index >= clock - pd.Timedelta(hours=1)) & (bars[s].index < clock)
                ]
                cap = participation * float(history.quote_volume.sum()) / 12 / previous[s]
                qty = min(budget / previous[s], cap)
                entries[s] = {"q": qty, "side": int(np.sign(w)), "time": clock.isoformat()}
                reserved += qty * previous[s]
                entry_requests += 1
                wanted += budget
                requests.append(
                    {
                        "time": clock.isoformat(),
                        "symbol": s,
                        "side": int(np.sign(w)),
                        "requested_q": budget / previous[s],
                        "submitted_q": qty,
                        "reference_price": previous[s],
                        "addition": p is not None,
                    }
                )
        if hourly:
            last_risk_scale = risk_scale
        fill_clock = clock + pd.Timedelta(minutes=5)
        for s in sorted(pending):
            if s not in positions:
                raise ValueError("Pending exit lacks inventory")
            p = positions[s]
            request = pending[s]
            bar = data[s][i]
            price = bar[3] * (1 - p["side"] * slip)
            qty = min(p["q"], request["q"], participation * bar[4] / price)
            if qty <= 0:
                continue
            delta = p["side"] * qty * (price - p["price"]) - qty * price * fee
            cash += delta
            p["net_pnl"] += delta
            p["q"] -= qty
            request["q"] -= qty
            ledger.append(
                {
                    "type": "exit",
                    "time": fill_clock.isoformat(),
                    "submitted_at": request["time"],
                    "symbol": s,
                    "position_id": p["id"],
                    "side": p["side"],
                    "quantity": qty,
                    "price": price,
                    "fee": qty * price * fee,
                    "slippage_cost": qty * abs(price - bar[3]),
                    "notional": qty * price,
                    "quote_volume": bar[4],
                    "cash_delta": delta,
                    "reason": request["reason"],
                }
            )
            max_delay = max(
                max_delay, (fill_clock - pd.Timestamp(request["time"])).total_seconds() / 60
            )
            if p["q"] < 1e-12:
                trades.append(
                    {
                        "symbol": s,
                        "direction": p["side"],
                        "entry_time": p["opened_at"],
                        "exit_time": fill_clock.isoformat(),
                        "entry_price": p["price"],
                        "exit_price": price,
                        "net_pnl": p["net_pnl"],
                        "position_id": p["id"],
                        "reason": request["reason"],
                    }
                )
                del positions[s]
                del pending[s]
            elif request["q"] < 1e-12:
                del pending[s]
            else:
                exit_partial += 1
        for s, request in entries.items():
            bar = data[s][i]
            price = bar[3] * (1 + request["side"] * slip)
            if s not in positions and len(positions) >= config["max_positions"]:
                continue
            marks = {name: data[name][i][3] for name in symbols}
            actual_gross = sum(p["q"] * marks[name] for name, p in positions.items())
            limit = config["gross"] * risk_scale
            cost_per_unit = price * fee + request["side"] * (price - bar[3]) + bar[3] * (fee + slip)
            available_quantity = max(
                0.0, (value(marks) * limit - actual_gross) / (bar[3] + limit * cost_per_unit)
            )
            qty = min(request["q"], participation * bar[4] / price, available_quantity)
            if qty <= 1e-12 or qty * price <= max(1e-8, capital * 1e-12):
                continue
            partial += bool(qty < request["q"] - 1e-12)
            charge = qty * price * fee
            cash -= charge
            old = positions.get(s)
            if old:
                if old["side"] != request["side"] or s in pending:
                    raise ValueError("Addition overlaps a reversal or pending exit")
                total = old["q"] + qty
                old["price"] = (old["q"] * old["price"] + qty * price) / total
                old["q"] = total
                old["net_pnl"] -= charge
                new_stop = price * (1 - request["side"] * config["stop_loss"])
                old["stop"] = (
                    max(old["stop"], new_stop) if old["side"] == 1 else min(old["stop"], new_stop)
                )
            else:
                sequence += 1
                positions[s] = {
                    "id": sequence,
                    "q": qty,
                    "side": request["side"],
                    "price": price,
                    "stop": price * (1 - request["side"] * config["stop_loss"]),
                    "net_pnl": -charge,
                    "opened_at": fill_clock.isoformat(),
                }
            filled += qty * price
            ledger.append(
                {
                    "type": "entry",
                    "time": fill_clock.isoformat(),
                    "submitted_at": request["time"],
                    "symbol": s,
                    "position_id": positions[s]["id"],
                    "addition": old is not None,
                    "side": request["side"],
                    "quantity": qty,
                    "price": price,
                    "fee": charge,
                    "slippage_cost": qty * abs(price - bar[3]),
                    "notional": qty * price,
                    "quote_volume": bar[4],
                    "cash_delta": -charge,
                }
            )
        previous = {s: data[s][i][3] for s in symbols}
        nav = value(previous)
        peak = max(peak, nav)
        curve.append(
            {
                "date": fill_clock,
                "equity": nav,
                "cash": cash,
                "gross_exposure": sum(p["q"] * previous[s] for s, p in positions.items()),
                "net_exposure": sum(p["side"] * p["q"] * previous[s] for s, p in positions.items()),
            }
        )
    return {
        "curve": pd.DataFrame(curve),
        "events": ledger,
        "trades": trades,
        "requests": requests,
        "positions": positions,
        "cash": cash,
        "pending_exits": pending,
        "capacity": {
            "entry_requests": entry_requests,
            "partially_filled_entries": int(partial),
            "partial_exit_intervals": exit_partial,
            "max_exit_delay_minutes": max_delay,
            "requested_entry_notional": wanted,
            "filled_entry_notional": filled,
            "filled_request_ratio": filled / wanted if wanted else None,
            "participation_cap": participation,
            "actual_order_book_verified": False,
        },
        "risk_halted": halted,
        "resume_state": {
            "config": deepcopy(config),
            "symbols": symbols,
            "next_clock": pd.Timestamp(end, tz="UTC").isoformat(),
            "cash": cash,
            "positions": deepcopy(positions),
            "pending_exits": deepcopy(pending),
            "sequence": sequence,
            "peak": peak,
            "risk_halted": halted,
            "last_risk_scale": last_risk_scale,
            "previous_prices": previous,
            "counters": {
                "entry_requests": entry_requests,
                "partial": partial,
                "exit_partial": exit_partial,
                "wanted": wanted,
                "filled": filled,
                "max_delay": max_delay,
            },
        },
        "engine": "five_minute_capacity_research_proxy_not_native_or_real_exchange",
    }
