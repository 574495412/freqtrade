"""Independent event replay for two-leg cash, capacity, inventory and NAV."""

import math
from collections import defaultdict
from itertools import groupby
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, sha


def close(actual, expected, label):
    if not math.isclose(actual, expected, abs_tol=2e-8, rel_tol=1e-12):
        raise ValueError(f"{label}: {actual} vs {expected}")


def price_equity(cash, legs, markets, btc, clock, fee, slip):
    net = cash
    for (campaign, side), leg in legs.items():
        market = btc if side == 1 else markets[campaign]
        rate = market.mark_close[market.index(clock)] * (1 - side * slip)
        net += leg["quantity"] * (side * (rate - leg["entry"]) - fee * rate)
    return net


def check_entries(entries, cash, legs, markets, btc, clock, plan, fee, slip):
    for symbol, batch in groupby(entries, key=lambda row: row["campaign"]):
        alt, hedge = list(batch)
        if (alt["symbol"], alt["side"], hedge["symbol"], hedge["side"]) != (
            symbol,
            -1,
            "BTCUSDT",
            1,
        ):
            raise ValueError("Pair lacks one long and one short")
        if clock != markets[symbol].launch + 289 or (symbol, -1) in legs:
            raise ValueError("Entry violates listing eligibility or exclusivity")
        if markets[symbol].day_quote < plan["day_quote_minimum"]:
            raise ValueError("Entry violates first-day turnover filter")
        alt_value = alt["qty"] * alt["rate"]
        btc_value = hedge["qty"] * hedge["rate"]
        close(btc_value / alt_value, plan["btc_notional_per_short"], "initial hedge ratio")
        net = price_equity(cash, legs, markets, btc, clock, fee, slip)
        used = math.fsum(v["quantity"] * v["entry"] for v in legs.values())
        budget = min(
            net * plan["stake_fraction"],
            net * plan["margin_fraction"] - used,
            (cash - used) / (1 + fee),
        )
        if alt_value + btc_value > budget + 2e-8:
            raise ValueError("Paired entry exceeds collateral or wallet budget")
        for event, tape in ((alt, markets[symbol]), (hedge, btc)):
            side = event["side"]
            idx = tape.index(clock)
            close(event["rate"], tape.close[idx] * (1 + side * slip), "entry fill")
            maximum = plan["volume_participation"] * tape.quote[idx - 12 : idx].mean()
            if event["qty"] * event["rate"] > maximum + 2e-8:
                raise ValueError("Entry exceeded known trailing turnover")
            delta = -fee * event["qty"] * event["rate"]
            close(event["cash_delta"], delta, "entry cash")
            cash += delta
            legs[(symbol, side)] = {
                "entry": event["rate"],
                "quantity": event["qty"],
                "opened": clock,
            }
    return cash


def funding_reference(legs, markets, btc, clock):
    expected = {}
    for (symbol, side), leg in legs.items():
        tape = btc if side == 1 else markets[symbol]
        opening_ms = leg["opened"] * 300_000
        for actual, rate, mark in tape.funding.get(clock, ()):
            if pd.Timestamp(actual).value // 1_000_000 > opening_ms:
                expected[(symbol, side, actual)] = (rate, mark, leg["quantity"])
    return expected


def update_positions(events, cash, legs, markets, btc, clock, plan, fee, slip):
    funding = funding_reference(legs, markets, btc, clock)
    fractions = defaultdict(list)
    for event in events:
        symbol, side = event["campaign"], event["side"]
        tape = btc if side == 1 else markets[symbol]
        if tape.name != event["symbol"]:
            raise ValueError("Ledger used wrong instrument")
        leg = legs[(symbol, side)]
        kind, quantity, rate = (event[k] for k in ("kind", "qty", "rate"))
        if kind == "funding":
            r, m, q = funding.pop((symbol, side, event["actual"]))
            close(q, quantity, "settled inventory")
            close(m, rate, "settled mark")
            close(r, event["funding"], "settled rate")
            delta = -side * q * r * m
        elif kind == "exit":
            i = tape.index(clock)
            if quantity > leg["quantity"] + 2e-8:
                raise ValueError("Exit exceeds held inventory")
            fractions[symbol].append(quantity / leg["quantity"])
            close(rate, tape.close[i] * (1 - side * slip), "exit fill")
            delta = quantity * (side * (rate - leg["entry"]) - fee * rate)
            leg["quantity"] -= quantity
        elif kind == "collateral_loss":
            i = tape.index(clock)
            extreme = tape.mark_low[i] if side == 1 else tape.mark_high[i]
            if side * (1 - extreme / leg["entry"]) < plan["margin_loss_mark_increase"]:
                raise ValueError("Untriggered collateral-loss event")
            close(quantity, leg["quantity"], "full collateral quantity")
            delta = -quantity * leg["entry"]
            leg["quantity"] = 0
        else:
            raise ValueError(f"Unrecognized event {kind}")
        close(event["cash_delta"], delta, "cash ledger")
        cash += delta
        if kind != "funding" and leg["quantity"] <= 1e-10:
            del legs[(symbol, side)]
    if funding:
        raise ValueError("Modeled ledger omitted expected settlement")
    for values in fractions.values():
        if len(values) == 2:
            close(values[0], values[1], "paired partial fraction")
    return cash


def audit(plan, path: Path, markets, btc, cost, summary):  # noqa: C901 -- explicit ledger
    nav = pd.read_parquet(path / "nav.parquet").set_index("clock")
    start, end = (
        int(pd.Timestamp(plan[k], tz="UTC").timestamp()) // 300
        for k in ("common_start", "common_end")
    )
    np.testing.assert_array_equal(nav.index.to_numpy(), np.arange(start, end + 1))
    ledger = read(path / "events.json")
    steps = defaultdict(list)
    for event in ledger:
        steps[event["clock"]].append(event)
    fee = plan["fees_bps"] / 10000
    slip = (cost - plan["fees_bps"]) / 10000
    net = np.zeros(len(nav))
    cash = peak = plan["capital"]
    legs = {}
    halted = False
    for index, (clock, snapshot) in enumerate(zip(nav.index, nav.to_numpy(), strict=True)):
        events = steps.get(clock, ())
        existing = [e for e in events if e["kind"] != "entry"]
        entries = [e for e in events if e["kind"] == "entry"]
        cash = update_positions(existing, cash, legs, markets, btc, clock, plan, fee, slip)
        value = price_equity(cash, legs, markets, btc, clock, fee, slip)
        peak = max(peak, value)
        halted |= value < peak * (1 - plan["maximum_observed_drawdown"])
        if entries and halted:
            raise ValueError("Account entered after drawdown halt")
        if entries:
            cash = check_entries(entries, cash, legs, markets, btc, clock, plan, fee, slip)
        turnover = defaultdict(float)
        for event in events:
            if event["kind"] in ("entry", "exit"):
                turnover[event["symbol"]] += event["qty"] * event["rate"]
        for symbol, notional in turnover.items():
            tape = btc if symbol == "BTCUSDT" else markets[symbol]
            if notional > plan["volume_participation"] * tape.quote[tape.index(clock)] + 2e-8:
                raise ValueError("Aggregate interval capacity exceeded")
        net[index] = price_equity(cash, legs, markets, btc, clock, fee, slip)
        peak = max(peak, net[index])
        count = len({symbol for symbol, _ in legs})
        close(cash, snapshot[0], "portfolio cash")
        if count != snapshot[2] or count > plan["maximum_slots"]:
            raise ValueError("Campaign count differs")
    error = float(np.max(np.abs(net - nav.equity)))
    if error > 2e-7 or legs or halted != summary["halted"]:
        raise ValueError(f"Portfolio parity failed: {error}")
    close(net[-1] / net[0] - 1, summary["net_return"], "net return")
    close(float((1 - net / np.maximum.accumulate(net)).max()), summary["max_drawdown"], "drawdown")
    attribution = defaultdict(float)
    campaign_cash = defaultdict(float)
    for event in ledger:
        asset = "btc" if event["side"] == 1 else "short"
        attribution[f"{asset}_{event['kind']}"] += event["cash_delta"]
        campaign_cash[event["campaign"]] += event["cash_delta"]
    completed = read(path / "closed.json")
    if len(completed) != len(campaign_cash) or len(completed) != summary["completed"]:
        raise ValueError("Campaign book count differs")
    for trade in completed:
        close(campaign_cash[trade["symbol"]], trade["net_usdt"], "per-campaign cash")
    return {
        "nav_points": len(nav),
        "maximum_equity_error_usdt": error,
        "events": len(ledger),
        "attribution_usdt": dict(attribution),
        "files": {item.name: sha(item) for item in sorted(path.iterdir())},
    }
