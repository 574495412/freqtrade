"""Verify A04 sizing, matched contracts, saved NAV and native order cash flows."""

import json
import math
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from research.crypto.nfi_strength_allocation_20261107.register import HERE, OUT, PLAN
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked,
    save,
    sha,
)


D = Decimal
TOP_COINS = {"141", "142", "143", "144", "145"}
CONTRACT_FIELDS = (
    "engine_commit",
    "engine_manifest",
    "start",
    "end",
    "comparison_end",
    "pairs",
    "datadir",
    "data_receipts",
    "allowed_leverage",
    "initial_equity_usdt",
    "periods",
    "market_snapshot",
    "environment",
    "runner_sha256",
    "observer_source",
)


def read(path):
    return json.loads(Path(path).read_text())


def native_trades(plan, path, strategy, cost):
    (archive_path,) = (path / "native_results").glob("backtest-result-*.zip")
    with ZipFile(archive_path) as archive:
        (name,) = [
            s for s in archive.namelist() if s.endswith(".json") and not s.endswith("_config.json")
        ]
        stats = json.loads(archive.read(name), parse_float=Decimal)["strategy"][strategy]
    if (
        stats["pairlist"] != plan["pairs"]
        or stats["starting_balance"] != plan["initial_equity_usdt"]
    ):
        raise ValueError("Native account contract mismatch")
    expected = D(cost) / 10000
    pnl = D(0)
    maximum_error = D(0)
    fills = 0
    trades = stats["trades"]
    for trade in trades:
        if not (
            trade["fee_open"] == expected == trade["fee_close"]
            and trade["leverage"] == plan["allowed_leverage"]
        ):
            raise ValueError("Unexpected native fee or leverage")
        entry_qty = exit_qty = entry_gross = exit_gross = D(0)
        for order in trade["orders"]:
            qty, price = order["amount"], order["safe_price"]
            if order["ft_is_entry"]:
                entry_qty += qty
                entry_gross += qty * price
            else:
                exit_qty += qty
                exit_gross += qty * price
        direction = -1 if trade["is_short"] else 1
        net = (
            direction * (exit_gross - entry_gross)
            - expected * (entry_gross + exit_gross)
            + trade["funding_fees"]
        )
        error = abs(net - trade["profit_abs"])
        if abs(entry_qty - exit_qty) > D("0.00000001") or error > D("0.00001"):
            raise ValueError(f"Trade cash failed to reconcile: {trade['pair']}")
        maximum_error = max(maximum_error, error)
        pnl += net
        fills += len(trade["orders"])
    aggregate_error = abs(pnl - stats["profit_total_abs"])
    if aggregate_error > D("0.0001"):
        raise ValueError("Aggregate cash failed to reconcile")
    return trades, {
        "archive_sha256": sha(archive_path),
        "trades": len(trades),
        "filled_orders": fills,
        "maximum_cash_error_usdt": float(maximum_error),
        "aggregate_cash_error_usdt": float(aggregate_error),
    }


def independent_ratio(pair, day, ago, closes, plan, receipts):
    for symbol in (pair, "BTC/USDT:USDT"):
        if symbol not in closes:
            symbol_code = symbol.replace("/", "_").replace(":", "_")
            relative = f"futures/{symbol_code}-1d-futures.feather"
            candle_path = checked(
                {
                    "path": str(Path(plan["datadir"]) / relative),
                    "sha256": receipts[relative],
                }
            )
            closes[symbol] = pd.read_feather(candle_path).set_index("date").close
    asset = closes[pair]
    btc = closes["BTC/USDT:USDT"]
    if all(t in series.index for series in (asset, btc) for t in (day, ago)):
        return float((asset.at[day] * btc.at[ago]) / (asset.at[ago] * btc.at[day]))
    return None


def size_audit(plan, path, strategy, trades):
    receipts = read(checked(plan["data_receipts"]))
    closes = {}
    buckets = Counter()
    decision_keys = set()
    audit_file = path / "stake-decisions.jsonl"
    for line in audit_file.read_text().splitlines():
        row = json.loads(line)
        pair, tag = row["pair"], row["tag"]
        if pair not in plan["pairs"] or not set(tag.split()) <= TOP_COINS or not tag.split():
            raise ValueError("Sizing applied outside the registered sleeve")
        clock = pd.Timestamp(row["time"])
        day = clock.normalize() - pd.Timedelta(days=1)
        ago = day - pd.Timedelta(days=30)
        ratio = None
        if strategy == "StrengthAllocation":
            ratio = independent_ratio(pair, day, ago, closes, plan, receipts)
            multiplier = 1.0
            if ratio is not None and ratio >= 1.05:
                multiplier = 1.75
            elif ratio is not None and ratio < 1.0:
                multiplier = 0.65
        else:
            multiplier = 1.25
        known = ratio is None and row["relative_30d"] is None
        if ratio is not None and row["relative_30d"] is not None:
            known = math.isclose(row["relative_30d"], ratio, abs_tol=1e-12)
        requested = min(
            row["max_stake"], max(row["min_stake"] or 0, row["native_stake"] * multiplier)
        )
        if (
            not known
            or row["multiplier"] != multiplier
            or not math.isclose(row["requested_stake"], requested, abs_tol=1e-9)
        ):
            raise ValueError(f"Sizing diverged from raw candles and bounds: {row}")
        decision_keys.add((pair, int(clock.timestamp() * 1000), tag.strip()))
        buckets[
            str(multiplier) if ratio is not None or strategy == "FlatTopBoost" else "unknown"
        ] += 1
    scoped = [
        t
        for t in trades
        if not t["is_short"] and (tags := set((t["enter_tag"] or "").split())) and tags <= TOP_COINS
    ]
    if not scoped or not buckets:
        raise ValueError("No sizing decisions or filled campaigns")
    unmatched = [
        t
        for t in scoped
        if (t["pair"], t["open_timestamp"], t["enter_tag"].strip()) not in decision_keys
    ]
    if unmatched:
        raise ValueError(f"Top Coins fills missing a first-stake decision: {len(unmatched)}")
    return {
        "stake_trace_sha256": sha(audit_file),
        "attempted_stakes_by_multiplier": dict(buckets),
        "matched_filled_top_coin_campaigns": len(scoped),
        "top_coin_native_pnl_usdt": float(sum(t["profit_abs"] for t in scoped)),
    }


def account(strategy, cost):
    plan = read(PLAN)
    candidate = next(case for case in plan["candidates"] if case["strategy"] == strategy)
    baseline = plan["baselines"][str(cost)]
    for source in baseline.values():
        checked(source)
    parent = read(checked(baseline["registered_plan.json"]))
    if any(plan[k] != parent[k] for k in CONTRACT_FIELDS):
        raise ValueError("Candidate and native baseline contracts differ")
    original = next(c for c in parent["candidates"] if c["name"] == "nfi-x8-1x")
    config = read(checked(candidate["config"]))
    if {**config, "strategy": original["strategy"]} != read(checked(original["config"])):
        raise ValueError("Underlying native config differs")
    path = OUT / f"{strategy}_{cost}bps"
    if read(path / "registered_plan.json") != plan:
        raise ValueError("Account registration mismatch")
    for name, source in candidate["source_files"].items():
        if sha(path / "strategies" / name) != source["sha256"]:
            raise ValueError("Native strategy copy differs")
    trades, ledger = native_trades(plan, path, strategy, cost)
    sizing = size_audit(plan, path, strategy, trades)
    result = read(path / "result.json")
    curve = path / "hourly_equity.parquet"
    frame = pd.read_parquet(curve).set_index("date")
    changes = {}
    old = read(checked(baseline["result.json"]))
    for window, reported, reference in zip(
        plan["periods"], result["periods"], old["periods"], strict=True
    ):
        start, end = (pd.Timestamp(window[k]) for k in ("start", "end"))
        equity = frame.loc[start:end, "hourly_mark_equity"]
        if not equity.index.equals(pd.date_range(start, end, freq="h")) or (equity <= 0).any():
            raise ValueError("Bad NAV calendar or nonpositive equity")
        net = float(equity.iloc[-1] / equity.iloc[0] - 1)
        dd = float((1 - equity / equity.cummax()).max())
        if not (
            math.isclose(net, reported["net_return"], abs_tol=1e-10)
            and math.isclose(dd, reported["hourly_mark_drawdown"], abs_tol=1e-10)
        ):
            raise ValueError("NAV economics differ")
        changes[window["name"]] = {
            "net_return": net,
            "return_change": net - reference["net_return"],
            "max_drawdown": dd,
            "drawdown_change": dd - reference["hourly_mark_drawdown"],
            "baseline_return": reference["net_return"],
            "baseline_drawdown": reference["hourly_mark_drawdown"],
            "hours": len(equity),
        }
    audit = {
        "plan_sha256": sha(PLAN),
        "audit_source_sha256": sha(Path(__file__)),
        "curve_sha256": sha(curve),
        "result_sha256": sha(path / "result.json"),
        **ledger,
        **sizing,
        "periods": changes,
    }
    save(path / "a04-audit.json", audit)
    print(strategy, cost, changes["continuous"], flush=True)
    return audit


def complete():
    report = {
        f"{s}_{c}bps": account(s, c)
        for c in (15, 8)
        for s in ("StrengthAllocation", "FlatTopBoost")
    }
    failed = {}
    for cost in (15, 8):
        strength = report[f"StrengthAllocation_{cost}bps"]["periods"]
        flat = report[f"FlatTopBoost_{cost}bps"]["periods"]["continuous"]
        checks = {
            **{f"beats_x8_{k}": v["return_change"] > 0 for k, v in strength.items()},
            "beats_flat_boost": strength["continuous"]["net_return"] > flat["net_return"],
            "within_drawdown_budget": strength["continuous"]["drawdown_change"] <= 0.01,
        }
        failed[str(cost)] = [k for k, passes in checks.items() if not passes]
    save(
        HERE / "result.json",
        {
            "accounts": report,
            "failed_gates": failed,
            "historical_screen_passed": not any(failed.values()),
            "paper_eligible": False,
        },
    )


if __name__ == "__main__":
    if len(sys.argv) == 1:
        complete()
    else:
        account(sys.argv[1], int(sys.argv[2]))
