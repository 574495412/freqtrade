"""Reconcile native orders, marks, funding, and fixed allocations after replay."""

import argparse
from collections import Counter, defaultdict
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

from research.crypto.donchian60_tail_native_20261125_v1.run import checked, read, save, sha
from research.crypto.head_shoulders_financial_native_20261117_v3.independent_account_audit_v2 import (
    cash_from_orders,
    fixed_combo,
    missing_exposure,
    periods_for,
    trade_path,
)
from research.crypto.nfi40_allocation_native_20261117_v1.profit_distribution import distribution


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PARENT = HERE.parent / "head_shoulders_financial_native_20261117_v3"
NAME = "Turtle55Close"
DAY_MS = 86400000


def curve(path, grid):
    table = pd.read_parquet(path)
    assert pd.DatetimeIndex(table.date).as_unit("ns").equals(grid)
    return table, pd.Series(table.hourly_mark_equity.to_numpy(), index=grid)


def audit_signal(trade, daily, open_price):
    orders = trade["orders"]
    entries, exit_order = orders[:-1], orders[-1]
    assert 1 <= len(entries) <= 4 and all(o["ft_is_entry"] for o in entries)
    assert not exit_order["ft_is_entry"]
    first_fill = entries[0]["order_filled_timestamp"]
    assert first_fill == trade["open_timestamp"] and first_fill % DAY_MS == 0
    rows = daily.set_index(pd.DatetimeIndex(daily.date).as_unit("ms").asi8 + DAY_MS)
    position = rows.index.get_loc(first_fill)
    current = rows.iloc[position]
    prior = rows.iloc[position - 55 : position]
    short = trade["is_short"]
    assert len(prior) == 55
    assert current.close < prior.low.min() if short else current.close > prior.high.max()
    assert trade["enter_tag"] == ("turtle55_short" if short else "turtle55_long")

    # Independent Wilder recurrence up to the information cutoff.
    prefix = rows.iloc[: position + 1]
    vol = [float(prefix.high.iloc[0] - prefix.low.iloc[0])]
    for old, new in zip(prefix.itertuples(index=False), prefix.iloc[1:].itertuples(index=False)):
        vol.append(max(new.high - new.low, abs(new.high - old.close), abs(new.low - old.close)))
    n = sum(vol[:20]) / 20
    for value in vol[20:]:
        n = (19 * n + value) / 20

    side = -1 if short else 1
    for previous, order in zip(entries, entries[1:]):
        now = order["order_filled_timestamp"]
        assert now > previous["order_filled_timestamp"] and now % DAY_MS == 0
        last = previous["safe_price"]
        assert side * (rows.loc[now, "close"] - last) >= n / 2 - 1e-9
        assert side * (open_price.loc[now] - last) >= n / 2 - 1e-9
        assert order["amount"] <= entries[0]["amount"] + 1e-8

    when = exit_order["order_filled_timestamp"]
    assert when == trade["close_timestamp"]
    if trade["exit_reason"] == "turtle20":
        assert when % DAY_MS == 0
        position = rows.index.get_loc(when)
        current = rows.iloc[position]
        prior = rows.iloc[position - 20 : position]
        assert current.close > prior.high.max() if short else current.close < prior.low.min()
    else:
        assert trade["exit_reason"] in {"force_exit", "liquidation", "stop_loss", "trailing_stop_loss"}
    return len(entries) - 1


def account(name, plan, grid):
    output = ROOT / "user_data/crypto_quant" / HERE.name / name
    result = read(output / "result.json")
    process = read(HERE / f"{name}.process.json")
    assert process["returncode"] == 0 and process["result_sha256"] == sha(output / "result.json")
    assert result["source_callback_errors"] == 0 and result["orders_enabled"] is False
    capital, cost = result["initial_equity_usdt"], result["cost_bps_per_side"]
    archive, = (output / "native_results").glob("*.zip")
    assert sha(archive) == result["archive_sha256"]
    with ZipFile(archive) as data:
        assert data.testzip() is None
        trades = read_archive(data, archive)

    path = checked({"path": str(output / "hourly_equity.parquet"), "sha256": result["hourly_curve_sha256"]})
    capture, observed = curve(path, grid)
    assert observed.iloc[0] == capital
    preflight = read(HERE.parent / "donchian60_tail_native_20261125_v1/preflight.json")
    directory = Path(plan["datadir"]) / "futures"
    clocks = grid.as_unit("ms").asi8
    reconstructed = np.full(len(grid), float(capital))
    count = np.zeros(len(grid), dtype=int)
    by_pair, units_added = defaultdict(list), Counter()
    max_cash_error = max_funding_error = 0.0
    for trade in trades:
        by_pair[trade["pair"]].append(trade)
    for pair, campaigns in by_pair.items():
        stem = pair.replace("/", "_").replace(":", "_")
        daily = pd.read_feather(checked(preflight["daily_receipts"][pair]))
        detail = pd.read_feather(directory / (stem + "-5m-futures.feather"), columns=["date", "open"])
        opens = detail.set_index(pd.DatetimeIndex(detail.date).as_unit("ms").asi8).open
        marks = pd.read_feather(directory / (stem + "-1h-mark.feather")).set_index("date").open
        rates = pd.read_feather(directory / (stem + "-1h-funding_rate.feather"))
        prices = marks.reindex(grid).to_numpy()
        events = pd.DatetimeIndex(rates.date).as_unit("ms").asi8
        units = rates.funding_rate.to_numpy() * marks.reindex(rates.date).to_numpy()
        assert np.isfinite(prices).all() and np.isfinite(units).all() and (np.diff(events) > 0).all()
        for trade in campaigns:
            additions = audit_signal(trade, daily, opens)
            units_added[additions] += 1
            cash = cash_from_orders(trade)
            assert abs(float(cash["quantity_residual"])) < 1e-8
            max_cash_error = max(max_cash_error, float(cash["error"]))
            contribution, _, _, funding, _ = trade_path(trade, clocks, prices, events, units)
            max_funding_error = max(max_funding_error, abs(funding - trade["funding_fees"]))
            reconstructed += contribution
            count += (clocks > trade["open_timestamp"]) & (clocks <= trade["close_timestamp"])
    max_nav_error = float(np.abs(reconstructed - observed.to_numpy()).max())
    numeric = (
        max_nav_error < 3e-5 and max_cash_error < 1e-5 and max_funding_error < 1e-7
        and np.array_equal(count, capture.open_trades.to_numpy())
    )
    missing = [missing_exposure(trades, pair) for pair in ("AXS/USDT:USDT", "TRB/USDT:USDT")]
    verified = numeric and all(item["status"] != "UNKNOWN" for item in missing)
    return {
        "status": "VERIFIED" if verified else "UNKNOWN",
        "capital_usdt": capital,
        "cost_bps_per_side": cost,
        "periods": periods_for(observed, plan["periods"]),
        "native_distributions": distribution(archive, path, capital, cost, grid, strategy=NAME),
        "campaigns_by_count_of_additions": dict(sorted(units_added.items())),
        "entry_add_exit_clock_audit_passed": True,
        "independent_nav_error_usdt": max_nav_error,
        "independent_trade_cash_error_usdt": max_cash_error,
        "independent_model_funding_error_usdt": max_funding_error,
        "known_missing_funding_exposure": missing,
        "stop_execution_prices_independently_certified": False,
        "historical_rules_certified": False,
        "orders_enabled": False,
    }, observed


def read_archive(data, archive):
    import json

    return json.loads(data.read(archive.stem + ".json"))["strategy"][NAME]["trades"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cost", type=int, choices=(8, 15), required=True)
    args = parser.parse_args()
    plan = read(PARENT / "plan.json")
    grid = pd.date_range(plan["start"], plan["comparison_end"], freq="h").as_unit("ns")
    cost = args.cost
    name500, name1000 = (f"turtle55_{capital}_cost{cost}" for capital in (500, 1000))
    # An early receipt can be audited while the remaining funds/cost runs continue.
    report = {}
    for name in (name500, name1000):
        if (HERE / f"{name}.process.json").is_file():
            report[name], observed = account(name, plan, grid)
            save(HERE / f"{name}.audit.json", report[name])
            if name == name500:
                candidate = observed
    if name500 not in report:
        return
    baseline = next(row for row in plan["benchmark_nfi1000"] if row["cost_bps"] == cost)
    _, original = curve(checked(baseline["hourly_curve"]), grid)
    control = plan["nfi500_dependency"]
    nfi_name = f"nfi40_x8_500_cost{cost}"
    admitted = read(HERE.parent / "head_shoulders_audit_evidence_v3_20261118_v1"
                    / "audited_complete_accounts/admission.json")["nfi_accounts"][str(cost)]
    nfi_path = Path(control["output_root"]) / nfi_name / "hourly_equity.parquet"
    assert admitted["passed"] and admitted["candidate"] == nfi_name
    _, nfi = curve(checked({"path": str(nfi_path), "sha256": admitted["observed_nav_sha256"]}), grid)
    _, result = fixed_combo(candidate, nfi, original, plan["periods"], report[name500]["status"] == "VERIFIED")
    save(HERE / f"combination_cost{cost}.json", result)


if __name__ == "__main__":
    main()
