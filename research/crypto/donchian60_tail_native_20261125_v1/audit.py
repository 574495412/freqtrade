"""Independently reconcile completed trend wallets, using actual orders and marks."""

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

from research.crypto.head_shoulders_financial_native_20261117_v3.independent_account_audit_v2 import (
    cash_from_orders,
    fixed_combo,
    missing_exposure,
    periods_for,
    trade_path,
    two_fill_funding,
)
from research.crypto.nfi40_allocation_native_20261117_v1.profit_distribution import distribution

from .run import HERE, NAME, ROOT, checked, save, sha, verify_study


DAY_MILLISECONDS = 86400000
START, END = pd.Timestamp("2025-01-01T00:00:00Z"), pd.Timestamp("2026-08-31T23:00:00Z")


def check_owned_trade(trade, daily, opens):
    entry, close = trade["open_timestamp"], trade["close_timestamp"]
    assert entry % DAY_MILLISECONDS == 0 and len(trade["orders"]) == 2
    orders = trade["orders"]
    assert orders[0]["ft_is_entry"] and not orders[1]["ft_is_entry"]
    assert orders[0]["amount"] == orders[1]["amount"] and orders[0]["amount"] > 0
    assert orders[0]["order_filled_timestamp"] == entry
    assert orders[1]["order_filled_timestamp"] == close
    assert trade["leverage"] == 1 and not trade["is_open"]
    direction = -1 if trade["is_short"] else 1
    assert trade["enter_tag"] == ("dc60_short" if direction < 0 else "dc60_long")
    entry_day = pd.Timestamp(entry, unit="ms", tz="UTC").normalize() - pd.Timedelta(days=1)
    index = daily.date.searchsorted(entry_day)
    assert daily.date.iloc[index] == entry_day and index >= 90
    values = daily.close.to_numpy()
    window = values[index - 59 : index + 1]
    top, bottom = max(window), min(window)
    assert top > bottom and values[index] == (top if direction > 0 else bottom)
    assert math.isclose(orders[0]["safe_price"], opens.loc[entry], rel_tol=0, abs_tol=1e-8)
    trail = (top + bottom) / 2
    predicted = None
    endpoint = pd.Timestamp(close, unit="ms", tz="UTC")
    for i in range(index + 1, len(daily)):
        available = daily.date.iloc[i] + pd.Timedelta(days=1)
        if available > endpoint:
            break
        segment = values[i - 59 : i + 1]
        upper, lower = max(segment), min(segment)
        value = values[i]
        opposite = upper > lower and value == (lower if direction > 0 else upper)
        breach = value < trail if direction > 0 else value >= trail
        if opposite or breach:
            predicted = "dc60_opposite" if opposite else "dc60_trail", available
            break
        midpoint = (upper + lower) / 2
        trail = max(trail, midpoint) if direction > 0 else min(trail, midpoint)
    reason = trade["exit_reason"]
    if reason in {"dc60_opposite", "dc60_trail"}:
        assert predicted == (reason, endpoint), (trade["pair"], reason, predicted, endpoint)
        assert math.isclose(orders[1]["safe_price"], opens.loc[close], rel_tol=0, abs_tol=1e-8)
    else:
        assert predicted is None and reason in {"force_exit", "liquidation", "stop_loss"}
    return reason


def account(directory, capital, cost, plan, preflight):
    result = json.loads((directory / "result.json").read_text())
    completion = json.loads((directory / "process_receipt.json").read_text())
    assert completion["returncode"] == 0 and completion["result_sha256"] == sha(directory / "result.json")
    assert result["initial_equity_usdt"] == capital and result["cost_bps_per_side"] == cost
    assert result["source_callback_errors"] == 0 and result["orders_enabled"] is False
    config = json.loads((directory / "config.json").read_text())
    assert config["trading_mode"] == "futures" and config["margin_mode"] == "isolated"
    assert config["exchange"]["pair_whitelist"] == plan["pairs"]
    assert config["max_open_trades"] == 6 and config["timeframe_detail"] == "5m"
    archives = list((directory / "native_results").glob("*.zip"))
    assert len(archives) == 1 and sha(archives[0]) == result["archive_sha256"]
    with ZipFile(archives[0]) as zipped:
        assert zipped.testzip() is None
        payloads = [
            json.loads(zipped.read(name)) for name in zipped.namelist()
            if name.endswith(".json") and not name.endswith("_config.json")
        ]
    stats = next(data["strategy"][NAME] for data in payloads if "strategy" in data)
    capture_path = checked({"path": str(directory / "hourly_equity.parquet"), "sha256": result["hourly_curve_sha256"]})
    grid = pd.date_range(START, END, freq="h").as_unit("ns")
    clocks = grid.as_unit("ms").asi8
    assert len(grid) == 14592
    capture = pd.read_parquet(capture_path)
    assert pd.DatetimeIndex(capture.date).as_unit("ns").equals(grid)
    nav = np.full(len(grid), float(capital))
    counts = np.zeros(len(grid), dtype=int)
    reasons = defaultdict(int)
    trades = defaultdict(list)
    max_funding_error = max_cash_error = 0.0
    terminal_pnl = 0.0
    for trade in stats["trades"]:
        trades[trade["pair"]].append(trade)
    for pair, campaigns in trades.items():
        daily = pd.read_feather(checked(preflight["daily_receipts"][pair]))
        stem = pair.replace("/", "_").replace(":", "_")
        parent = Path(plan["datadir"]) / "futures"
        marks = pd.read_feather(parent / (stem + "-1h-mark.feather")).set_index("date").open
        prices = marks.reindex(grid).to_numpy()
        rates = pd.read_feather(parent / (stem + "-1h-funding_rate.feather"))
        events = pd.DatetimeIndex(rates.date).as_unit("ms").asi8
        units = rates.funding_rate.to_numpy() * marks.reindex(rates.date).to_numpy()
        assert np.isfinite(prices).all() and np.isfinite(units).all() and np.all(np.diff(events) > 0)
        detail = pd.read_feather(parent / (stem + "-5m-futures.feather"), columns=["date", "open"])
        opens = detail.set_index(pd.DatetimeIndex(detail.date).as_unit("ms").asi8).open
        for trade in campaigns:
            assert trade["fee_open"] == trade["fee_close"] == cost / 10000
            reasons[check_owned_trade(trade, daily, opens)] += 1
            cash = cash_from_orders(trade)
            terminal_pnl += float(cash["net_pnl"])
            max_cash_error = max(max_cash_error, float(cash["error"]))
            contribution, _, _, funding, _ = trade_path(trade, clocks, prices, events, units)
            _, funding_closed_form = two_fill_funding(trade, events, units, clocks)
            max_funding_error = max(max_funding_error, abs(funding - trade["funding_fees"]),
                                    abs(funding_closed_form - funding))
            nav += contribution
            counts += (clocks > trade["open_timestamp"]) & (clocks <= trade["close_timestamp"])
    maximum_nav_error = float(np.max(np.abs(nav - capture.hourly_mark_equity.to_numpy())))
    assert maximum_nav_error < 3e-5 and max_funding_error < 1e-7 and max_cash_error < 1e-5
    assert abs(terminal_pnl - stats["profit_total_abs"]) < 1e-4
    assert np.array_equal(counts, capture.open_trades.to_numpy())
    missing = [missing_exposure(stats["trades"], p) for p in ("AXS/USDT:USDT", "TRB/USDT:USDT")]
    reconstructed = pd.Series(nav, index=grid, name="independent_marked_nav")
    audit = {
        "status": "UNKNOWN" if any(row["status"] == "UNKNOWN" for row in missing) else "VERIFIED",
        "cost_bps_per_side": cost, "initial_equity_usdt": capital,
        "maximum_nav_error_usdt": maximum_nav_error, "maximum_funding_error_usdt": max_funding_error,
        "maximum_cash_error_usdt": max_cash_error, "missing_funding": missing,
        "independent_exit_reason_counts": dict(reasons),
        "periods": periods_for(reconstructed, plan["periods"]),
        "profit_distribution": distribution(archives[0], capture_path, capital, cost, grid, strategy=NAME),
        "conditional_old_execution_model": True, "historical_rules_certified": False,
        "native_risk_exit_prices_independently_certified": False, "orders_enabled": False,
    }
    return audit, reconstructed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--complete-accounts", action="store_true", required=True)
    parser.parse_args()
    plan, preflight, _ = verify_study()
    state = json.loads((HERE / "execution_state.json").read_text())
    if state["state"] != "COMPLETED" or len(state["completed"]) != 4:
        raise ValueError("Four accounts must reach final process receipts")
    output = ROOT / "user_data/crypto_quant" / HERE.name
    report, navs = {}, {}
    for capital, cost in ((1000, 15), (1000, 8), (500, 15), (500, 8)):
        name = f"dc60_{capital}_cost{cost}"
        evidence, values = account(output / name, capital, cost, plan, preflight)
        report[name], navs[name] = evidence, values
        print(name, evidence["status"], evidence["periods"][0]["net_return"], flush=True)
    nfi = plan["nfi500_dependency"]
    nfi_audit = json.loads((Path(nfi["execution_complete_path"]).parent / "audit_results.json").read_text())
    combos = []
    for baseline in plan["benchmark_nfi1000"]:
        cost = baseline["cost_bps"]
        frame = pd.read_parquet(checked(baseline["hourly_curve"]))
        original = pd.Series(frame.hourly_mark_equity.to_numpy(), index=pd.DatetimeIndex(frame.date).as_unit("ns"))
        nfi_name = f"nfi40_x8_500_cost{cost}"
        numeric = next(row for row in nfi_audit["accounts"] if row["candidate"] == nfi_name)
        directory = Path(nfi["output_root"]) / nfi_name
        source = checked({"path": str(directory / "hourly_equity.parquet"), "sha256": numeric["hourly_equity_sha256"]})
        actual = pd.read_parquet(source)
        nfi_curve = pd.Series(actual.hourly_mark_equity.to_numpy(), index=pd.DatetimeIndex(actual.date).as_unit("ns"))
        key = f"dc60_500_cost{cost}"
        _, result = fixed_combo(navs[key], nfi_curve, original, plan["periods"], False)
        result["cost_bps"] = cost
        result["candidate_numeric_verified"] = report[key]["status"] == "VERIFIED"
        result["nfi_numeric_verified"] = numeric["passed"]
        result["passes_economic_comparison"] = (
            all(row["net_return"] > 0 for row in result["periods"])
            and result["periods"][0]["net_return"] > result["nfi1000_periods"][0]["net_return"]
            and result["periods"][0]["hourly_mark_drawdown"] <= result["nfi1000_periods"][0]["hourly_mark_drawdown"]
        )
        combos.append(result)
    save(HERE / "independent_results.json", {"accounts": report, "combinations": combos, "orders_enabled": False})


if __name__ == "__main__":
    main()
