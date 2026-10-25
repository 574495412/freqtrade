"""Independently reconcile complete native fills and synchronous portfolio valuations."""

import argparse
import json
import math
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from freqtrade.research.data import read, save, sha


def raw_trades(folder, candidate):
    archives = list(folder.glob("backtest-result-*.zip"))
    if len(archives) != 1:
        raise ValueError("Ambiguous native archive")
    with ZipFile(archives[0]) as archive:
        names = [
            n for n in archive.namelist() if n.endswith(".json") and not n.endswith("_config.json")
        ]
        if len(names) != 1:
            raise ValueError("Ambiguous native trade document")
        document = json.loads(archive.read(names[0]))
    return document["strategy"][candidate]["trades"]


def fill_cash(trade):
    cash, inventory = Decimal(0), Decimal(0)
    direction = Decimal(-1 if trade["is_short"] else 1)
    for order in trade["orders"]:
        quantity, price = Decimal(str(order["amount"])), Decimal(str(order["safe_price"]))
        entry = order["ft_is_entry"]
        fee = Decimal(str(trade["fee_open"] if entry else trade["fee_close"]))
        cash += quantity * price * (-direction if entry else direction) - quantity * price * fee
        inventory += quantity if entry else -quantity
    cash += Decimal(str(trade["funding_fees"]))
    if abs(inventory) > Decimal("0.000001"):
        raise ValueError("Native filled inventory did not end flat")
    error = abs(cash - Decimal(str(trade["profit_abs"])))
    if error > Decimal("0.00001"):
        raise ValueError("Native order cash and recorded funding did not reconcile")
    return float(error)


def closed_drawdown(trades, initial):
    closed = defaultdict(list)
    for trade in trades:
        closed[trade["close_timestamp"]].append(trade["profit_abs"])
    cash = peak = initial
    drawdown = 0.0
    for clock in sorted(closed):
        cash += math.fsum(closed[clock])
        peak = max(peak, cash)
        drawdown = max(drawdown, 1 - cash / peak)
    return drawdown


def audit_account(run, plan, phase, cost):
    folder = run / phase / plan["candidate"] / f"cost_{cost}bps"
    result = read(folder / "result.json")
    ledger = read(folder / "trade_ledger.json")
    target = pd.read_parquet(folder / "hourly_equity.parquet")
    source_trades, values, positions = [], [], []
    maximum_fill_error = 0.0
    for component in plan["components"]:
        source_run = Path(component["run"])
        source = source_run / phase / component["name"] / f"cost_{cost}bps"
        for name, expected in component["file_identities"].items():
            if (
                name.startswith(str(source.relative_to(source_run)) + "/")
                and sha(source_run / name) != expected
            ):
                raise ValueError("Native input receipt changed")
        trades = raw_trades(source, component["name"])
        for index, trade in enumerate(trades):
            maximum_fill_error = max(maximum_fill_error, fill_cash(trade))
            source_trades.append(dict(trade, component=component["name"], native_trade_index=index))
        curve = pd.read_parquet(source / "hourly_equity.parquet")
        if (
            curve.date.dt.as_unit("ns").astype("int64").tolist()
            != target.date.iloc[1:].dt.as_unit("ns").astype("int64").tolist()
        ):
            raise ValueError("Portfolio clock alignment changed")
        values.append(curve.equity.tolist())
        positions.append(curve.open_trades.tolist())
    if source_trades != ledger:
        raise ValueError("Portfolio selected, scaled or changed a native trade")
    equity = [plan["initial_equity_usdt"]] + [
        math.fsum(items) for items in zip(*values, strict=True)
    ]
    counts = [0] + [sum(items) for items in zip(*positions, strict=True)]
    equity_error = max(abs(a - b) for a, b in zip(equity, target.equity, strict=True))
    if equity_error > 1e-8 or counts != target.open_trades.tolist():
        raise ValueError("Full native NAV or inventory counts differ from portfolio")
    peak, observed_dd = equity[0], 0.0
    for value in equity:
        peak = max(peak, value)
        observed_dd = max(observed_dd, 1 - value / peak)
    closed_dd = closed_drawdown(source_trades, plan["initial_equity_usdt"])
    positive = math.fsum(max(t["profit_abs"], 0) for t in source_trades)
    negative = -math.fsum(min(t["profit_abs"], 0) for t in source_trades)
    profit = math.fsum(t["profit_abs"] for t in source_trades)
    target_rules = plan["screening_targets"]
    passed = (
        len(source_trades) >= target_rules["minimum_closed_trades_per_evaluation"]
        and profit > 0
        and positive >= target_rules["minimum_profit_factor"] * negative
        and max(observed_dd, closed_dd) <= target_rules["maximum_observed_drawdown"]
    )
    if (
        abs(profit - result["net_profit_usdt"]) > 1e-5
        or abs(observed_dd - result["hourly_observed_drawdown"]) > 1e-10
    ):
        raise ValueError("Portfolio return or risk summary did not independently reproduce")
    if passed != result["economic_audit"]["passed"]:
        raise ValueError("Portfolio economic classification did not independently reproduce")
    return {
        "phase": phase,
        "cost": cost,
        "full_trades_reproduced": len(source_trades),
        "hourly_nav_rows_reproduced": len(equity),
        "maximum_nav_error_usdt": equity_error,
        "maximum_fill_cash_error_usdt": maximum_fill_error,
        "economic_screen_independently_reproduced": True,
        "economic_screen_passed": passed,
        "portfolio_result_sha256": sha(folder / "result.json"),
    }


def audit(run):
    if (run / "portfolio_audit.json").exists():
        raise ValueError("Portfolio audit already exists")
    plan = read(run / "plan.json")
    rows = [
        audit_account(run, plan, phase, cost)
        for phase in ("train", "validation", "holdout", "continuous")
        for cost in plan["extra_cost_bps_per_side"]
    ]
    summary = {
        "passed": all(r["economic_screen_passed"] for r in rows),
        "accounts": rows,
        "plan_sha256": sha(run / "plan.json"),
        "auditor_sha256": sha(Path(__file__)),
        "scope": (
            "complete native fills with recorded funding; full synchronous hourly NAV; "
            "not a second fill engine, intrahour worst-case or independent alpha evidence"
        ),
        "independent_oos": False,
        "orders_enabled": False,
    }
    (run / "auditor_snapshot.py").write_bytes(Path(__file__).read_bytes())
    save(run / "portfolio_audit.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.run.resolve())
    print({"passed": result["passed"], "cost_period_accounts": len(result["accounts"])})
