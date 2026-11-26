"""Measure the native 562 trailing-exit price band around the recorded NEAR split."""

import ast
from decimal import ROUND_FLOOR, Decimal
from math import fsum
from pathlib import Path

import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .inputs import HERE
from .native_trade_path_review import books


PAIR = "NEAR/USDT:USDT"
MODE = "exit_profit_short_rebuy_t_0_1_m"
D = Decimal


def source_rule(path):
    tree = ast.parse(path.read_text())
    strategy, = [node for node in tree.body if isinstance(node, ast.ClassDef)
                 and node.name == "NostalgiaForInfinityX8"]
    scalp_tags, = [node.value for node in strategy.body if isinstance(node, ast.Assign)
                   and any(isinstance(name, ast.Name) and name.id == "short_scalp_mode_tags"
                           for name in node.targets)]
    if "562" in ast.literal_eval(scalp_tags):
        raise ValueError("Tag 562 belongs to the separate scalp exit branch")
    exit_target, = [node for node in strategy.body if isinstance(node, ast.FunctionDef)
                    and node.name == "exit_profit_target"]
    short, = [node for node in ast.walk(exit_target) if isinstance(node, ast.If)
              and ast.unparse(node.test) == "trade.is_short"]
    scalp = short.body[1]
    if not isinstance(scalp, ast.If) or ast.unparse(scalp.test) != "is_scalp_mode":
        raise ValueError("The short trailing-profit source branch changed")
    band = scalp.orelse[0]
    if not isinstance(band, ast.If) or ast.unparse(band.test) != (
        "0.001 <= profit_init_ratio < 0.01"
    ):
        raise ValueError("The short trailing-profit band changed")
    drawdown = band.body[0]
    if not isinstance(drawdown, ast.If) or ast.unparse(drawdown.test) != (
        "profit_init_ratio < previous_profit - 0.02"
    ):
        raise ValueError("The short trailing-profit drawdown predicate changed")
    return {"minimum": D("0.001"), "maximum": D("0.01"), "pullback": D("0.02"),
            "source_line": band.lineno}


def price_band(snapshot, entry, cost, tick, rule):
    current = D(str(snapshot["execution_open"]))
    ratio = D(str(snapshot["profit_per_initial_notional"]))
    peak = D(str(snapshot["cache_before"]["profit"]))
    if snapshot["cache_before"]["sell_reason"] != "exit_profit_short_rebuy_max":
        raise ValueError("The cached target is not the short-rebuy profit peak")
    lower = rule["minimum"]
    upper = min(rule["maximum"], peak - rule["pullback"])
    slope = (1 + D(cost) / 10_000) / entry
    exclusive = current + (ratio - upper) / slope
    inclusive = current + (ratio - lower) / slope
    first = int((exclusive / tick).to_integral_value(rounding=ROUND_FLOOR)) + 1
    last = int((inclusive / tick).to_integral_value(rounding=ROUND_FLOOR))
    if first <= last and upper > lower:
        tick_count = last - first + 1
        valid = [str(tick * first), str(tick * last)]
    else:
        tick_count, valid = 0, []
    scenario = []
    for steps in (-1, 0, 1):
        price = current + tick * steps
        value = ratio - slope * (price - current)
        scenario.append({
            "price": str(price), "profit_to_initial_notional": str(value),
            "meets_profit_floor": value >= lower,
            "meets_cached_peak_pullback": value < peak - rule["pullback"],
            "meets_this_band": lower <= value < upper,
        })
    return {"open_interval_lower_price": str(exclusive),
            "closed_interval_upper_price": str(inclusive),
            "accepted_tick_count": tick_count, "accepted_tick_bounds": valid,
            "maximum_allowed_profit_ratio_exclusive": str(upper),
            "peak_profit_ratio": str(peak), "adjacent_ticks": scenario}


def campaigns(native, wallet):
    return {
        key: {
            "exit": trade["exit_reason"].partition(" (")[0],
            "net_usdt": wallet["trades"][key]["profit"],
            "additions": wallet["trades"][key]["entry_fills"] - 1,
        }
        for key, trade in native.items() if key in wallet["trades"] and key[1]
        and trade["enter_tag"].split() == ["562"]
    }


def subgroup(rows):
    chosen = [row for row in rows if row["exit"] == MODE]
    return {"all_natural_short_562": len(rows), "branch_natural_closes": len(chosen),
            "branch_net_usdt": fsum(row["net_usdt"] for row in chosen),
            "branch_without_additions": sum(row["additions"] == 0 for row in chosen)}


def main():
    exit_audit = receipt(HERE / "near_short_exit_audit.json")
    event = read(checked(exit_audit))
    frozen = read(checked(event["freeze"]))
    first = pd.Timestamp(frozen["observed_first_fill"])
    books_by_cost = {}
    by_fee = {}
    shared_market = None
    shared_strategy = None
    for cost in (8, 15):
        record = read(checked(frozen["accounts"][str(cost)]))["account"]
        plan = read(checked(record["registered_plan"]))
        strategy = plan["candidates"][0]["source_files"]["NostalgiaForInfinityX8.py"]
        market = plan["market_snapshot"]
        if shared_market is not None and (market, strategy) != (shared_market, shared_strategy):
            raise ValueError("The 8 and 15 bp venue and strategy input receipts differ")
        shared_market, shared_strategy = market, strategy
        wallet, native = books(read(checked(record["proof"])))
        trade = native[(PAIR, True, first)]
        books_by_cost[cost] = campaigns(native, wallet)
        venue = read(checked(market))["markets"][PAIR]
        price_rule, = [row for row in venue["info"]["filters"]
                       if row["filterType"] == "PRICE_FILTER"]
        tick = D(price_rule["tickSize"])
        if tick != D(str(venue["precision"]["price"])):
            raise ValueError("NEAR price tick definition is inconsistent")
        snapshot = event["results"][str(cost)]["callback_diagnostic"]["snapshots"][-1]
        rule = source_rule(checked(strategy))
        first_price = D(str(trade["orders"][0]["safe_price"]))
        band = price_band(snapshot, first_price, cost, tick, rule)
        if band["adjacent_ticks"][1]["meets_this_band"] != (snapshot["exit_reason"] is not None):
            raise ValueError("The mathematical price band disagrees with the native callback")
        rows = books_by_cost[cost]
        by_fee[str(cost)] = {
            "price_tick": str(tick), "first_fill_price": str(first_price), "near_price_band": band,
            "by_first_fill_year": {
                str(year): subgroup([item for key, item in rows.items() if key[-1].year == year])
                for year in (2025, 2026)
            },
            "full_account": subgroup(list(rows.values())),
        }
    low, high = (books_by_cost[cost] for cost in (8, 15))
    differences = [
        {"pair": key[0], "first_fill": key[-1].isoformat(), "8bp": low[key], "15bp": high[key]}
        for key in sorted(low.keys() & high.keys())
        if (low[key]["exit"] == MODE) != (high[key]["exit"] == MODE)
    ]
    write_once(HERE / "short_exit_price_band.json", {
        "source": receipt(Path(__file__)), "native_exit_audit": exit_audit,
        "market_snapshot": shared_market, "native_strategy": shared_strategy,
        "source_rule": {name: str(value) if isinstance(value, D) else value
                        for name, value in rule.items()},
        "by_cost": by_fee, "matched_first_fills_with_different_branch_membership": differences,
        "scope": (
            "Observed NEAR state at the early exit, moving only the current decision price. "
            "The previous cached peak and funding are fixed. This does not alter historical "
            "fee, candle, execution, or account data."
        ),
        "orders_enabled": False,
    })
    print({cost: details["near_price_band"]["accepted_tick_count"]
           for cost, details in by_fee.items()})


if __name__ == "__main__":
    main()
