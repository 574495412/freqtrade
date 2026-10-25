"""GitHub-factor research replay; preserve the earlier frozen replay versions.

Adds target dollar neutrality and measured actual net exposure; preserves the
original factor_portfolio.py and its frozen64-account study source identity.

No exchange API, strategy import or .env. Scores must have a timestamp <= the
order decision; next-interval volume changes fills only. Model fitting belongs
to TradingAgents, while this module owns the crypto account economics.
"""

import math

import numpy as np
import pandas as pd


def weights(scores, volatility, gross, threshold, max_positions, neutral=False):
    eligible = {s: float(v) for s, v in scores.items() if np.isfinite(v) and abs(v) > threshold}
    if neutral:
        # Separate side budgets; this is a target, not a guarantee of simultaneous fills.
        count = max_positions // 2
        longs = sorted((s for s in eligible if eligible[s] > 0), key=lambda s: (-eligible[s], s))[
            :count
        ]
        shorts = sorted((s for s in eligible if eligible[s] < 0), key=lambda s: (eligible[s], s))[
            :count
        ]
        if not longs or not shorts:
            return {}
        out = {}
        for names, side in ((longs, 1), (shorts, -1)):
            raw = {s: 1 / max(float(volatility.get(s, 0.01)), 0.001) for s in names}
            for s in names:
                out[s] = side * gross / 2 * raw[s] / sum(raw.values())
        return out
    chosen = sorted(eligible, key=lambda s: (-abs(eligible[s]), s))[:max_positions]
    raw = {s: np.sign(eligible[s]) / max(float(volatility.get(s, 0.01)), 0.001) for s in chosen}
    denominator = sum(abs(x) for x in raw.values())
    return {s: float(gross * v / denominator) for s, v in raw.items()} if denominator else {}


def rank_buffer_weights(scores, held, gross, side_count=8, keep_buffer=4):
    """Rank hysteresis uses actual inventory; no synthetic held-signal state."""
    ordered = sorted(
        (s for s, v in scores.items() if np.isfinite(v) and v != 0), key=lambda s: (-scores[s], s)
    )
    wide = side_count + keep_buffer
    # Keep the two retention bands disjoint in a small eligible universe.
    if len(ordered) < 2 * wide:
        return {}
    left, right = ordered[:wide], ordered[-wide:]
    longs = [s for s in left if held.get(s, {}).get("side") == 1]
    shorts = [s for s in reversed(right) if held.get(s, {}).get("side") == -1]
    longs = (longs + [s for s in ordered[:side_count] if s not in longs])[:side_count]
    shorts = (shorts + [s for s in reversed(ordered[-side_count:]) if s not in shorts])[:side_count]
    if set(longs) & set(shorts):
        raise ValueError("Carry sleeves overlap")
    return {
        **{s: gross / 2 / side_count for s in longs},
        **{s: -gross / 2 / side_count for s in shorts},
    }


def replay(bars, funding, scores, config, start, end):  # noqa: C901
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
    cash = capital
    positions = {}
    pending = {}
    sequence = 0
    halted = False
    peak = capital
    ledger = []
    trades = []
    requests = []
    curve = []
    entry_requests = 0
    partial = 0
    exit_partial = 0
    wanted = 0.0
    filled = 0.0
    max_delay = 0.0

    def value(prices):
        return cash + math.fsum(
            p["side"] * p["q"] * (prices[s] - p["price"]) - p["q"] * prices[s] * (fee + slip)
            for s, p in positions.items()
        )

    curve.append(
        {
            "date": times[0],
            "equity": capital,
            "cash": cash,
            "gross_exposure": 0.0,
            "net_exposure": 0.0,
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
                pending.setdefault(
                    s,
                    {
                        "q": p["q"],
                        "time": clock.isoformat(),
                        "reason": "risk_halt" if halted else "stop",
                    },
                )
        if clock.minute == 0 and clock.hour % config["rebalance_hours"] == 0:
            row = index.get(clock)
            if row is None:
                raise ValueError("Missing timestamped portfolio decision")
            if config.get("control") == "cash":
                target = {}
            elif config.get("control") == "long_only":
                target = weights(
                    {s: 1.0 for s in symbols},
                    {s: row.get("vol_" + s, 0.01) for s in symbols},
                    config["gross"],
                    0,
                    config["max_positions"],
                )
            else:
                sc = {s: row[s] for s in symbols}
                vol = {s: row.get("vol_" + s, 0.01) for s in symbols}
                target = (
                    rank_buffer_weights(
                        sc, positions, config["gross"], config["side_count"], config["keep_buffer"]
                    )
                    if config.get("weight_mode") == "rank_buffer" and not halted
                    else weights(
                        sc,
                        vol,
                        config["gross"],
                        config["threshold"],
                        config["max_positions"],
                        config.get("neutral_target", False),
                    )
                    if not halted
                    else {}
                )
            gross = sum(p["q"] * previous[s] for s, p in positions.items())
            reserved = 0.0
            for s in symbols:
                p = positions.get(s)
                w = target.get(s, 0.0)
                desired = abs(w) * equity / previous[s]
                if p:
                    if s in pending and pending[s]["reason"] in ("stop", "risk_halt"):
                        continue
                    # Existing lifecycles can only shrink or close.
                    q = min(p["q"], desired) if np.sign(w) == p["side"] else 0.0
                    if p["q"] > q + 1e-12:
                        pending[s] = {
                            "q": p["q"] - q,
                            "time": clock.isoformat(),
                            "reason": "rebalance",
                        }
                    continue
                if not w or len(positions) + len(entries) >= config["max_positions"]:
                    continue
                budget = min(
                    desired * previous[s], max(0, equity * config["gross"] - gross - reserved)
                )
                if budget <= max(1e-8, equity * 1e-12):
                    # Floating-point budget dust must not create counted lifecycles.
                    continue
                # Submitted size is bounded solely by the preceding complete hour turnover.
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
                    }
                )
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
            qty = min(request["q"], participation * bar[4] / price)
            if qty <= 0:
                continue
            partial += qty < request["q"] - 1e-12
            sequence += 1
            charge = qty * price * fee
            cash -= charge
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
                    "position_id": sequence,
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
        "engine": "five_minute_capacity_research_proxy_not_native_or_real_exchange",
    }
