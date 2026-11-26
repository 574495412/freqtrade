"""Independent actual-fill/cash/NAV audit of the unchanged conditional NFI40 model.

No native engine, strategy, or execution-runner imports. Funding bookkeeping is
an independent replay of exported fills and frozen rate/mark rows, following the
pinned historical engine's hourly refresh and order-transfer semantics. It does
not reinterpret that model as a venue settlement convention.
"""

import ast
import hashlib
import json
import math
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd


HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]
HOUR = 3600000


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def extract(path, name):
    """Only copy the named independent diagnostic function, never import owner."""
    tree = ast.parse(Path(path).read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(nodes) == 1
    scope = {
        "Decimal": Decimal,
        "D": Decimal,
        "localcontext": localcontext,
        "SLOT": 1782273600000,
        "datetime": datetime,
        "UTC": UTC,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), scope)  # noqa: S102 - sealed local diagnostic AST only
    return scope[name]


def metrics(nav):
    assert np.isfinite(nav).all() and (nav > 0).all()
    daily = nav.reindex(pd.date_range(nav.index[0], nav.index[-1], freq="24h"))
    changes = daily.pct_change(fill_method=None).dropna()
    deviation = changes.std(ddof=1)
    years = (nav.index[-1] - nav.index[0]).total_seconds() / (365 * 86400)
    return {
        "net_return": float(nav.iloc[-1] / nav.iloc[0] - 1),
        "initial_marked_equity": float(nav.iloc[0]),
        "ending_marked_equity": float(nav.iloc[-1]),
        "hourly_mark_drawdown": float((1 - nav / nav.cummax()).max()),
        "cagr_365day": float((nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1),
        "complete_24h_daily_nav_sharpe": float(changes.mean() / deviation * math.sqrt(365))
        if deviation > 0
        else None,
        "complete_24h_returns": len(changes),
        "calendar_hours": len(nav),
    }


def funding_history(trade, events, units, clocks):
    """Cash audit only: hourly refresh, then immediate source fills in array order.

    At a fill, native close_bt_order transfers the previous running amount to
    the order. Entry recalc and exit recalc then differ from a venue cashflow
    convention. Boundary DCA is retained and explicitly inventoried; equality
    with the final native funding and every saved NAV is required for admission.
    Minified exports lack order creation timestamps, so deferred boundary entry
    ordering is not certified independently of those aggregate cross-checks.
    """
    orders = trade["orders"]
    first, last = orders[0]["order_filled_timestamp"], orders[-1]["order_filled_timestamp"]
    grouped = {}
    for order in orders:
        grouped.setdefault(order["order_filled_timestamp"], []).append(order)
    hours = range(((first + HOUR - 1) // HOUR) * HOUR, last + 1, HOUR)
    times = sorted(set(hours) | set(grouped))
    quantity = Decimal(0)
    stored = running = total = 0.0
    last_fill = first
    history_times, history_values, boundary = [], [], []
    sign = 1 if trade["is_short"] else -1
    count = 0

    def fees(start, end, amount):
        lo, hi = np.searchsorted(events, [start, end], side="left")
        if hi < len(events) and events[hi] == end:
            hi += 1
        return sign * math.fsum((units[lo:hi] * float(amount)).tolist())

    for when in times:
        if quantity > 0 and when % HOUR == 0:
            running = fees(last_fill, when, quantity)
            total = stored + running
        for order in grouped.get(when, []):
            if when in events:
                boundary.append({"time_ms": when, "is_entry": order["ft_is_entry"], "index": count})
            initial = count == 0
            stored += running
            running = 0.0
            amount = Decimal(str(order["amount"]))
            if order["ft_is_entry"]:
                quantity += amount
                running = fees(when, when, quantity)
                # Native _enter_trade's final recalc drops the running component
                # from funding_fees, without clearing funding_fee_running.
                total = stored
                if initial and when % HOUR == 0:
                    # Initial entry is followed by _check_trade_exit that hour.
                    total = stored + running
            else:
                running = fees(when, when, quantity)
                quantity -= amount
                total = stored
            last_fill = when
            count += 1
        history_times.append(when)
        history_values.append(total)
    before = np.searchsorted(history_times, clocks, side="left")
    accrued = np.asarray([0.0, *history_values])[before]
    return accrued, total, boundary


def account(candidate, plan, grid, cash_oracle, exposure):
    directory = Path(plan["output_root"]) / candidate["name"]
    receipt = read(directory / "process_receipt.json")
    assert receipt["returncode"] == 0
    assert sha(directory / "result.json") == receipt["result_sha256"]
    archives = list((directory / "native_results").glob("*.zip"))
    assert len(archives) == 1
    with ZipFile(archives[0]) as archive:
        assert archive.testzip() is None
        payloads = [
            json.loads(archive.read(n))
            for n in archive.namelist()
            if n.endswith(".json") and not n.endswith("_config.json")
        ]
        native = [p for p in payloads if "strategy" in p]
        assert len(native) == 1 and list(native[0]["strategy"]) == [candidate["strategy"]]
        stats = native[0]["strategy"][candidate["strategy"]]
    config = read(directory / "config.json")
    assert config["dry_run_wallet"] == candidate["capital"] == 500
    assert config["fee"] == candidate["cost_bps"] / 10000
    assert config["max_open_trades"] == 6
    assert config["exchange"]["pair_whitelist"] == plan["pairs"] == stats["pairlist"]
    for name, record in candidate["source_files"].items():
        assert sha(directory / "strategies" / name) == record["sha256"]
    frame = pd.read_parquet(directory / "hourly_equity.parquet")
    assert pd.DatetimeIndex(frame.date).as_unit("ns").equals(grid)
    clocks = grid.as_unit("ms").asi8
    total = np.full(len(grid), 500.0)
    gross = np.zeros(len(grid))
    signed = np.zeros(len(grid))
    counts = np.zeros(len(grid), dtype=int)
    closed_cash = np.zeros(len(grid))
    inputs, data = {}, {}
    for pair in plan["pairs"]:
        stem = pair.replace("/", "_").replace(":", "_")
        markpath = Path(plan["datadir"]) / "futures" / (stem + "-1h-mark.feather")
        fundingpath = markpath.with_name(stem + "-1h-funding_rate.feather")
        marks = pd.read_feather(markpath).set_index("date").open
        funding = pd.read_feather(fundingpath)
        events = pd.DatetimeIndex(funding.date).as_unit("ms").asi8
        units = funding.funding_rate.to_numpy() * marks.reindex(funding.date).to_numpy()
        prices = marks.reindex(grid).to_numpy()
        assert np.isfinite(prices).all() and np.isfinite(units).all()
        assert np.all(np.diff(events) > 0) and np.all(events % HOUR == 0)
        data[pair] = (prices, events, units)
        inputs[str(markpath)] = sha(markpath)
        inputs[str(fundingpath)] = sha(fundingpath)
    cash_rows, funding_rows, boundaries = [], [], []
    residuals = []
    fills = 0
    for number, trade in enumerate(stats["trades"]):
        assert trade["leverage"] == 1 and trade["pair"] in plan["pairs"]
        assert trade["fee_open"] == trade["fee_close"] == config["fee"]
        row = cash_oracle(trade)
        row["trade_index"] = number
        cash_rows.append(row)
        residual = Decimal(row["quantity_residual"])
        assert abs(residual) < Decimal("1e-8")
        if residual:
            residuals.append({"trade_index": number, "quantity": str(residual)})
        orders = trade["orders"]
        times = np.asarray([o["order_filled_timestamp"] for o in orders], dtype=np.int64)
        assert np.all(np.diff(times) >= 0) and all(o["amount"] > 0 for o in orders)
        assert not orders[-1]["ft_is_entry"]
        fills += len(orders)
        q, bought, sold = Decimal(0), Decimal(0), Decimal(0)
        quantities, entries, exits = [0.0], [0.0], [0.0]
        for order in orders:
            amount = Decimal(str(order["amount"]))
            value = amount * Decimal(str(order["safe_price"]))
            if order["ft_is_entry"]:
                q += amount
                bought += value
            else:
                q -= amount
                sold += value
            quantities.append(float(q))
            entries.append(float(bought))
            exits.append(float(sold))
        before = np.searchsorted(times, clocks, side="left")
        held = np.asarray(quantities)[before]
        # A closed native campaign cannot become a new position from decimal
        # export residuals; the exact residual remains reported above.
        held[clocks > trade["close_timestamp"]] = 0.0
        assert held.min() > -1e-8
        entry = np.asarray(entries)[before]
        exit_value = np.asarray(exits)[before]
        prices, events, units = data[trade["pair"]]
        accrued, terminal_funding, boundary = funding_history(trade, events, units, clocks)
        funding_rows.append(
            {
                "trade_index": number,
                "pair": trade["pair"],
                "reconstructed_usdt": terminal_funding,
                "native_usdt": trade["funding_fees"],
                "absolute_error_usdt": abs(terminal_funding - trade["funding_fees"]),
            }
        )
        boundaries.extend({"trade_index": number, "pair": trade["pair"], **b} for b in boundary)
        direction = -1 if trade["is_short"] else 1
        contribution = direction * (exit_value + held * prices - entry)
        contribution -= entry * config["fee"] + exit_value * config["fee"]
        contribution -= held * prices * config["fee"]
        contribution += accrued
        total += contribution
        gross += held * prices
        signed += direction * held * prices
        counts += (clocks > trade["open_timestamp"]) & (clocks <= trade["close_timestamp"])
        closed_cash += (clocks > trade["close_timestamp"]) * float(row["reconstructed_net_pnl"])
    observed = frame.hourly_mark_equity.to_numpy()
    errors = total - observed
    nav = pd.Series(observed, index=grid)
    reported = read(directory / "result.json")
    periods = []
    for period, original in zip(plan["periods"], reported["periods"], strict=True):
        calculated = {"name": period["name"], **metrics(nav.loc[period["start"] : period["end"]])}
        for key in ("net_return", "hourly_mark_drawdown", "calendar_hours"):
            assert abs(calculated[key] - original[key]) < 1e-12
        periods.append(calculated)
    assert (
        abs(
            (1 + periods[1]["net_return"]) * (1 + periods[2]["net_return"])
            - 1
            - periods[0]["net_return"]
        )
        < 1e-12
    )
    maximum_cash = max(float(r["absolute_error"]) for r in cash_rows)
    maximum_funding = max(r["absolute_error_usdt"] for r in funding_rows)
    unknown = [exposure(stats["trades"], pair) for pair in ("AXS/USDT:USDT", "TRB/USDT:USDT")]
    checks = {
        "full_hourly_nav": float(np.max(np.abs(errors))) < 3e-5,
        "campaign_cash": maximum_cash < 1e-5,
        "aggregate_cash": abs(
            math.fsum(float(r["reconstructed_net_pnl"]) for r in cash_rows)
            - stats["profit_total_abs"]
        )
        < 1e-4,
        "native_funding_bookkeeping": maximum_funding < 1e-7,
        "hourly_open_campaigns": bool(np.array_equal(counts, frame.open_trades.to_numpy())),
        "closed_campaign_cash_path": float(np.max(np.abs(closed_cash - frame.closed_realized_pnl)))
        < 1e-4,
        "unknown_funding_entire_brackets_flat": all(
            r["no_modeled_exposure_in_entire_bracket"] for r in unknown
        ),
        "native_terminal_cash": abs(stats["final_balance"] - 500 - stats["profit_total_abs"])
        < 1e-6,
    }
    artifact = {
        "candidate": candidate["name"],
        "passed": all(checks.values()),
        "checks": checks,
        "periods": periods,
        "actual_native_trades": len(stats["trades"]),
        "filled_orders": fills,
        "multi_order_trades": sum(len(t["orders"]) > 2 for t in stats["trades"]),
        "maximum_nav_error_usdt": float(np.max(np.abs(errors))),
        "maximum_campaign_cash_error_usdt": maximum_cash,
        "maximum_funding_error_usdt": maximum_funding,
        "maximum_gross_exposure_to_nav": float(np.max(gross / observed)),
        "maximum_abs_net_exposure_to_nav": float(np.max(np.abs(signed) / observed)),
        "native_terminal_balance": stats["final_balance"],
        "native_funding_usdt": math.fsum(t["funding_fees"] for t in stats["trades"]),
        "native_forced_exits": sum(t["exit_reason"] == "force_exit" for t in stats["trades"]),
        "open_campaigns_at_common_endpoint": int(counts[-1]),
        "funding_boundary_fills": boundaries,
        "unknown_funding_exposure": unknown,
        "closed_serialization_quantity_residuals": residuals,
        "archive_sha256": sha(archives[0]),
        "hourly_equity_sha256": sha(directory / "hourly_equity.parquet"),
        "input_sha256": inputs,
        "conditional_old_execution_model": True,
        "funding_scope": "Original inclusive native bookkeeping, not a corrected venue funding convention",
        "deferred_boundary_entry_order_creation_metadata_available": False,
        "sharpe_note": "Complete 24h UTC midnight returns; original runner used daily last observations",
        "production_eligible": False,
        "orders_enabled": False,
    }
    save(directory / "independent_cash_rows.json", cash_rows)
    save(directory / "independent_funding_rows.json", funding_rows)
    pd.DataFrame(
        {
            "date": grid,
            "reconstructed_nav": total,
            "native_observed_nav": observed,
            "difference": errors,
            "gross": gross,
            "signed": signed,
        }
    ).to_parquet(directory / "independent_nav.parquet", index=False)
    save(directory / "independent_account_audit.json", artifact)
    return artifact


def main():
    frozen = read(HERE / "audit_freeze.json")
    for path, digest in frozen["files"].items():
        assert sha(path) == digest, path
    plan = read(HERE / "plan.json")
    assert (HERE / "execution_complete.json").exists(), "Both native accounts must finish first"
    receipts = read(plan["data_receipts"]["path"])
    for relative, digest in receipts.items():
        assert sha(Path(plan["datadir"]) / relative) == digest
    cash = extract(frozen["cash_oracle_path"], "cash_from_orders")
    exposure = extract(frozen["exposure_oracle_path"], "exposure")
    grid = pd.date_range(plan["start"], plan["comparison_end"], freq="h").as_unit("ns")
    rows = [account(c, plan, grid, cash, exposure) for c in plan["candidates"]]
    result = {
        "passed": all(r["passed"] for r in rows),
        "accounts": rows,
        "financial_reruns": 0,
        "audit_code_sha256": sha(__file__),
    }
    save(HERE / "audit_results.json", result)
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "accounts": [
                    {
                        k: r[k]
                        for k in (
                            "candidate",
                            "passed",
                            "checks",
                            "maximum_nav_error_usdt",
                            "maximum_funding_error_usdt",
                        )
                    }
                    for r in rows
                ],
            }
        ),
        flush=True,
    )
    if not result["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
