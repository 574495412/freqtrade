"""Two-leg perpetual research account; no atomic fill fiction or live orders.

Signals carry already frozen pair models. Independent leg fills can fail the
hedge; real inventory then exits in subsequent intervals with all costs paid.
"""

import math
from copy import deepcopy

import numpy as np
import pandas as pd


def replay(bars, funding, signals, config, start, end, initial_state=None):  # noqa: C901
    capital = float(config["capital"])
    fee, slip = config["fee_bps"] / 10000, config["slippage_bps"] / 10000
    participation, budget = config["participation"], config["gross"]
    if not 0 < participation <= 1 or not 0 <= budget <= 1 or fee < 0 or slip < 0:
        raise ValueError("Invalid unlevered account or costs")
    symbols = sorted(bars)

    def utc(value):
        clock = pd.Timestamp(value)
        return clock.tz_localize("UTC") if clock.tzinfo is None else clock.tz_convert("UTC")

    begin, finish = utc(start), utc(end)
    if any(t.second or t.microsecond or t.minute % 5 for t in (begin, finish)):
        raise ValueError("Account boundaries must align to five minutes")
    clocks = pd.date_range(begin, finish, freq="5min")
    if len(clocks) < 2:
        raise ValueError("Nonempty account required")
    times = clocks[:-1]
    data = {
        s: bars[s].loc[times, ["open", "high", "low", "close", "quote_volume"]].to_numpy()
        for s in symbols
    }
    previous_bar = {
        s: bars[s]
        .loc[times[0] - pd.Timedelta(minutes=5), ["open", "high", "low", "close", "quote_volume"]]
        .to_numpy()
        for s in symbols
    }
    rates = {s: funding[s].to_dict("index") for s in symbols}
    decisions = {pd.Timestamp(r["time"]): r for r in signals}
    if len(decisions) != len(signals):
        raise ValueError("Duplicate signal clocks")
    state = deepcopy(initial_state) if initial_state else {}
    if state and (
        state["next_clock"] != times[0].isoformat()
        or state["config"] != config
        or state["symbols"] != symbols
    ):
        raise ValueError("Resume boundary/configuration/universe differs")
    if state and any(
        abs(float(previous_bar[s][3]) - state["previous_prices"][s]) > 1e-10 for s in symbols
    ):
        raise ValueError("Resume marks differ")
    cash = state.get("cash", capital)
    campaigns = state.get("campaigns", {})
    sequence = state.get("sequence", 0)
    peak = state.get("peak", capital)
    halted = state.get("risk_halted", False)
    rearm = state.get("rearm", {})
    counters = state.get(
        "counters",
        {
            "entry_requests": 0,
            "partial_entries": 0,
            "hedge_failures": 0,
            "partial_exit_intervals": 0,
            "wanted": 0.0,
            "filled": 0.0,
            "max_exit_delay_minutes": 0.0,
        },
    )
    ledger = []
    trades = []
    requests = []
    curve = []

    def mark(s, i, previous=False):
        return float(previous_bar[s][3] if previous and i == 0 else data[s][i - int(previous)][3])

    def exposures(i, previous=False):
        gross = net = floating = reserve = 0.0
        for p in campaigns.values():
            for s, leg in p["legs"].items():
                price = mark(s, i, previous)
                gross += leg["q"] * price
                net += leg["side"] * leg["q"] * price
                floating += leg["side"] * leg["q"] * (price - leg["price"])
                reserve += leg["q"] * price * (fee + slip)
        return gross, net, floating - reserve

    def nav(i, previous=False):
        return cash + exposures(i, previous)[2]

    def record(clock, i, previous=False):
        gross, net, floating = exposures(i, previous)
        curve.append(
            {
                "date": clock,
                "equity": cash + floating,
                "cash": cash,
                "gross_exposure": gross,
                "net_exposure": net,
            }
        )

    def request_exit(pair, clock, reason):
        priority = {"risk_halt": 5, "leg_stop": 4, "hedge_failure": 3, "z_stop": 2}
        old = pair.get("exit_request")
        if old is None:
            pair["exit_request"] = {"time": clock.isoformat(), "reason": reason}
        elif priority.get(reason, 0) > priority.get(old["reason"], 0):
            old["reason"] = reason

    record(times[0], 0, True)
    for i, clock in enumerate(times):
        fill_clock = clocks[i + 1]
        for p in campaigns.values():
            for s, leg in p["legs"].items():
                rate = rates[s].get(clock)
                if leg["q"] > 0 and rate is not None:
                    delta = -leg["side"] * leg["q"] * rate["mark"] * rate["funding_rate"]
                    cash += delta
                    leg["net_pnl"] += delta
                    p["net_pnl"] += delta
                    ledger.append(
                        {
                            "type": "funding",
                            "time": clock.isoformat(),
                            "symbol": s,
                            "position_id": p["id"],
                            "pair_key": p["pair_key"],
                            "quantity": leg["q"],
                            "side": leg["side"],
                            "mark": rate["mark"],
                            "rate": rate["funding_rate"],
                            "cash_delta": delta,
                        }
                    )
        equity = nav(i, True)
        peak = max(peak, equity)
        if not math.isfinite(equity) or equity <= 0:
            raise ValueError("Insolvent/nonfinite pair account")
        if equity < peak * (1 - config["drawdown_halt"]):
            halted = True
        for p in campaigns.values():
            stopped = False
            for s, leg in p["legs"].items():
                b = previous_bar[s] if i == 0 else data[s][i - 1]
                if leg["q"] > 0 and pd.Timestamp(p["opened_at"]) < clock:
                    stopped |= bool(
                        b[2] <= leg["stop"] if leg["side"] == 1 else b[1] >= leg["stop"]
                    )
            if halted:
                request_exit(p, clock, "risk_halt")
            elif stopped:
                request_exit(p, clock, "leg_stop")
            elif (clock - pd.Timestamp(p["opened_at"])).total_seconds() / 3600 >= config[
                "maximum_hold_hours"
            ]:
                request_exit(p, clock, "maximum_hold")
        scheduled = clock.minute == 0 and clock.hour % config["decision_hours"] == 0
        row = decisions.get(clock) if scheduled else None
        if scheduled and row is None:
            raise ValueError("Missing timestamped pair signals")
        entries = []
        if scheduled:
            desired = {p["pair_key"]: p for p in row["pairs"]}
            names = [p[k] for p in row["pairs"] for k in ("x", "y")]
            if (
                len(set(names)) != len(names)
                or len(desired) > config["max_pairs"]
                or len(names) > config["max_positions"]
            ):
                raise ValueError("Signal pairs overlap assets or exceed count")
            for p in campaigns.values():
                signal = desired.get(p["pair_key"])
                if signal is None or signal["model_id"] != p["model_id"]:
                    request_exit(p, clock, "formation_change")
                elif not signal["signal_valid"]:
                    request_exit(p, clock, "unknown_signal")
                elif abs(signal["z"]) >= config["z_stop"]:
                    request_exit(p, clock, "z_stop")
                elif np.sign(p["entry_z"]) * signal["z"] <= config["z_exit"]:
                    request_exit(p, clock, "reversion")
            for key, signal in desired.items():
                if key in rearm and signal["signal_valid"] and abs(signal["z"]) <= config["z_exit"]:
                    rearm[key]["reset_seen"] = True
            owned = {s for p in campaigns.values() for s in p["symbols"]}
            available = max(0.0, equity * budget - exposures(i, True)[0])
            for key, signal in sorted(desired.items()):
                if (
                    halted
                    or not signal["entry_eligible"]
                    or signal["x"] in owned
                    or signal["y"] in owned
                ):
                    continue
                cool = rearm.get(key)
                if cool and (
                    not cool["reset_seen"]
                    or (clock - pd.Timestamp(cool["closed_at"])).total_seconds() / 3600
                    < config["rearm_hours"]
                ):
                    continue
                if len(campaigns) + len(entries) >= config["max_pairs"]:
                    continue
                gross = min(equity * budget / len(desired), available)
                if gross <= max(1e-8, capital * 1e-12):
                    continue
                beta = signal["beta"]
                if not math.isfinite(beta) or beta <= 0:
                    raise ValueError("Invalid hedge beta")
                weights = {signal["x"]: beta / (1 + beta), signal["y"]: 1 / (1 + beta)}
                sides = {
                    signal["x"]: int(np.sign(signal["z"])),
                    signal["y"]: -int(np.sign(signal["z"])),
                }
                quantity = {s: gross * w / mark(s, i, True) for s, w in weights.items()}
                fraction = 1.0
                for s, q in quantity.items():
                    history = bars[s].loc[
                        (bars[s].index >= clock - pd.Timedelta(hours=1)) & (bars[s].index < clock)
                    ]
                    if len(history) != 12:
                        raise ValueError("Missing completed-hour quote data")
                    cap = participation * float(history.quote_volume.sum()) / 12 / mark(s, i, True)
                    fraction = min(fraction, cap / q)
                submitted = {s: q * max(0, fraction) for s, q in quantity.items()}
                intent = {
                    "time": clock.isoformat(),
                    "pair_key": key,
                    "model_id": signal["model_id"],
                    "signal": deepcopy(signal),
                    "requested_q": quantity,
                    "submitted_q": submitted,
                    "reference_prices": {s: mark(s, i, True) for s in quantity},
                    "sides": sides,
                    "requested_notional": gross,
                }
                requests.append(intent)
                counters["entry_requests"] += 1
                counters["wanted"] += gross
                entries.append(intent)
                available -= sum(q * mark(s, i, True) for s, q in submitted.items())
                owned.update(quantity)
        # Exits are independently capacity limited. Ownership persists even when
        # only one leg remains; a scheduled new model cannot spend planned exits.
        for key, p in list(campaigns.items()):
            request = p.get("exit_request")
            if request is None:
                continue
            remaining = False
            for s, leg in p["legs"].items():
                if leg["q"] <= 0:
                    continue
                b = data[s][i]
                price = float(b[3] * (1 - leg["side"] * slip))
                q = min(leg["q"], participation * float(b[4]) / price)
                if q <= 0:
                    remaining = True
                    continue
                delta = leg["side"] * q * (price - leg["price"]) - q * price * fee
                cash += delta
                leg["net_pnl"] += delta
                p["net_pnl"] += delta
                leg["q"] -= q
                if leg["q"] < 1e-12:
                    leg["q"] = 0.0
                ledger.append(
                    {
                        "type": "exit",
                        "time": fill_clock.isoformat(),
                        "submitted_at": request["time"],
                        "symbol": s,
                        "pair_key": p["pair_key"],
                        "position_id": p["id"],
                        "side": leg["side"],
                        "quantity": q,
                        "price": price,
                        "fee": q * price * fee,
                        "slippage_cost": q * abs(price - b[3]),
                        "notional": q * price,
                        "quote_volume": float(b[4]),
                        "cash_delta": delta,
                        "reason": request["reason"],
                    }
                )
                counters["max_exit_delay_minutes"] = max(
                    counters["max_exit_delay_minutes"],
                    (fill_clock - pd.Timestamp(request["time"])).total_seconds() / 60,
                )
                remaining |= leg["q"] > 0
            if remaining:
                counters["partial_exit_intervals"] += 1
            else:
                trades.append(
                    {
                        "symbol": p["pair_key"],
                        "pair_key": p["pair_key"],
                        "position_id": p["id"],
                        "model_id": p["model_id"],
                        "direction": int(np.sign(p["entry_z"])),
                        "entry_time": p["opened_at"],
                        "exit_time": fill_clock.isoformat(),
                        "net_pnl": p["net_pnl"],
                        "reason": request["reason"],
                        "hedge_matched": p["hedge_matched"],
                        "leg_net_pnl": {s: leg["net_pnl"] for s, leg in p["legs"].items()},
                    }
                )
                rearm[p["pair_key"]] = {"closed_at": fill_clock.isoformat(), "reset_seen": False}
                del campaigns[key]
        for intent in entries:
            actual = {}
            prices = {}
            for s, q in intent["submitted_q"].items():
                b = data[s][i]
                price = float(b[3] * (1 + intent["sides"][s] * slip))
                prices[s] = price
                actual[s] = min(q, participation * float(b[4]) / price)
            base = sum(q * mark(s, i) for s, q in actual.items())
            drag = sum(
                q * (abs(prices[s] - mark(s, i)) + prices[s] * fee + mark(s, i) * (fee + slip))
                for s, q in actual.items()
            )
            factor = (
                min(1.0, max(0.0, (nav(i) * budget - exposures(i)[0]) / (base + budget * drag)))
                if base > 0
                else 0.0
            )
            actual = {s: q * factor for s, q in actual.items()}
            if not any(q > 1e-12 for q in actual.values()):
                continue
            fractions = [actual[s] / q if q > 0 else 0.0 for s, q in intent["submitted_q"].items()]
            matched = bool(min(fractions) > 0 and max(fractions) - min(fractions) <= 1e-8)
            sequence += 1
            signal = intent["signal"]
            pair = {
                "id": sequence,
                "pair_key": intent["pair_key"],
                "model_id": intent["model_id"],
                "symbols": sorted(actual),
                "beta": signal["beta"],
                "entry_z": signal["z"],
                "opened_at": fill_clock.isoformat(),
                "hedge_matched": matched,
                "net_pnl": 0.0,
                "legs": {},
            }
            for s, q in actual.items():
                if q <= 1e-12:
                    q = 0.0
                price = prices[s]
                side = intent["sides"][s]
                charge = q * price * fee
                pair["legs"][s] = {
                    "q": q,
                    "side": side,
                    "price": price,
                    "stop": price * (1 - side * config["stop_loss"]),
                    "net_pnl": -charge,
                    "entered_q": q,
                    "opened_at": fill_clock.isoformat(),
                }
                if q == 0:
                    continue
                cash -= charge
                pair["net_pnl"] -= charge
                counters["filled"] += q * price
                ledger.append(
                    {
                        "type": "entry",
                        "time": fill_clock.isoformat(),
                        "submitted_at": intent["time"],
                        "symbol": s,
                        "position_id": sequence,
                        "pair_key": intent["pair_key"],
                        "side": side,
                        "quantity": q,
                        "price": price,
                        "fee": charge,
                        "slippage_cost": q * abs(price - mark(s, i)),
                        "notional": q * price,
                        "quote_volume": float(data[s][i][4]),
                        "cash_delta": -charge,
                        "hedge_matched": matched,
                    }
                )
            campaigns[str(sequence)] = pair
            counters["partial_entries"] += bool(min(fractions) < 1 - 1e-8)
            if not matched:
                counters["hedge_failures"] += 1
                request_exit(pair, fill_clock, "hedge_failure")
        record(fill_clock, i)
        peak = max(peak, curve[-1]["equity"])
    positions = {
        s: {**deepcopy(leg), "id": p["id"], "pair_key": p["pair_key"]}
        for p in campaigns.values()
        for s, leg in p["legs"].items()
        if leg["q"] > 0
    }
    pending = {
        str(p["id"]): deepcopy(p["exit_request"])
        for p in campaigns.values()
        if p.get("exit_request")
    }
    return {
        "curve": pd.DataFrame(curve),
        "events": ledger,
        "trades": trades,
        "requests": requests,
        "positions": positions,
        "pair_positions": deepcopy(campaigns),
        "cash": cash,
        "pending_exits": pending,
        "capacity": {
            "entry_requests": counters["entry_requests"],
            "partially_filled_entries": counters["partial_entries"],
            "hedge_failures": counters["hedge_failures"],
            "partial_exit_intervals": counters["partial_exit_intervals"],
            "max_exit_delay_minutes": counters["max_exit_delay_minutes"],
            "requested_entry_notional": counters["wanted"],
            "filled_entry_notional": counters["filled"],
            "filled_request_ratio": counters["filled"] / counters["wanted"]
            if counters["wanted"]
            else None,
            "participation_cap": participation,
            "actual_order_book_verified": False,
        },
        "risk_halted": halted,
        "engine": "independent_two_leg_next5m_capacity_proxy_not_exchange_orders",
        "resume_state": {
            "config": deepcopy(config),
            "symbols": symbols,
            "next_clock": clocks[-1].isoformat(),
            "cash": cash,
            "campaigns": deepcopy(campaigns),
            "sequence": sequence,
            "peak": peak,
            "risk_halted": halted,
            "rearm": deepcopy(rearm),
            "counters": deepcopy(counters),
            "previous_prices": {s: float(data[s][-1][3]) for s in symbols},
        },
    }
