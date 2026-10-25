"""Frozen single-account experiment and objective-specific, net whole-trade scoring."""

import argparse
import math
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from itertools import pairwise
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.audit_ensemble import fill_cash, raw_trades


def sleeve_summary(trades):
    profit = math.fsum(t["profit_abs"] for t in trades)
    positive = math.fsum(max(t["profit_abs"], 0) for t in trades)
    negative = -math.fsum(min(t["profit_abs"], 0) for t in trades)
    notional = math.fsum(t["amount"] * t["open_rate"] for t in trades)
    return {
        "closed_positions": len(trades),
        "net_profit_usdt": profit,
        "win_rate": sum(t["profit_abs"] > 0 for t in trades) / len(trades) if trades else None,
        "profit_factor": positive / negative if negative else None,
        "gross_profit": positive,
        "gross_loss": negative,
        "entry_notional_weighted_win_rate": (
            math.fsum(t["amount"] * t["open_rate"] for t in trades if t["profit_abs"] > 0)
            / notional
            if notional
            else None
        ),
        "long_positions": sum(not t["is_short"] for t in trades),
        "short_positions": sum(t["is_short"] for t in trades),
        "mean_holding_minutes": (
            math.fsum(t["trade_duration"] for t in trades) / len(trades) if trades else None
        ),
    }


def joint_assessment(result, sleeves, plan):
    rules = plan["joint_targets"]
    days = (datetime.fromisoformat(result["end"]) - datetime.fromisoformat(result["start"])).days
    hurdle = (1 + rules["annual_compounded_net_return_target"]) ** (days / 365.25) - 1
    frequency = sleeves["small_pullback"]["closed_positions"] / days
    reasons = list(result["economic_audit"]["reasons"])
    if (result["win_rate"] or 0) < rules["minimum_net_whole_position_win_rate"]:
        reasons.append("完整平仓净胜率未达80percent研究目标")
    if result["net_return"] < hurdle:
        reasons.append("期间净收益未达50percent年化研究目标对应的复利门槛")
    if frequency < rules["minimum_small_complete_trades_per_calendar_day"]:
        reasons.append("小额层完整交易频率未达研究目标")
    if any(sleeves[tag]["net_profit_usdt"] <= 0 for tag in sleeves):
        reasons.append("至少一个仓位层扣费后不盈利")
    if sleeves["core_breakout"]["net_profit_usdt"] <= sleeves["small_pullback"]["net_profit_usdt"]:
        reasons.append("大额层净利润未超过小额层")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "period_return_hurdle": hurdle,
        "small_complete_positions_per_calendar_day": frequency,
    }


def account_metrics(folder, candidate, plan):
    result = read(folder / "result.json")
    trades = raw_trades(folder, candidate)
    if any(len(t["orders"]) != 2 for t in trades):
        raise ValueError("Whole-position strategy produced a partial or additional fill")
    error = max((fill_cash(t) for t in trades), default=0.0)
    if abs(math.fsum(t["profit_abs"] for t in trades) - result["net_profit_usdt"]) > 1e-5:
        raise ValueError("Whole-account fill cash differs from native result")
    sleeves = {
        tag: sleeve_summary([t for t in trades if t["enter_tag"] == tag])
        for tag in ["small_pullback", "core_breakout"]
    }
    if sum(s["closed_positions"] for s in sleeves.values()) != len(trades):
        raise ValueError("Unknown autonomous entry tag")
    curve = pd.read_parquet(folder / "hourly_equity.parquet").set_index("date").equity
    boundaries = pd.date_range(result["start"], result["end"], freq="MS", tz="UTC")
    monthly = []
    for begin, end in pairwise(boundaries):
        a = plan["initial_equity_usdt"] if begin == boundaries[0] else float(curve.loc[begin])
        monthly.append(
            {
                "start": begin.isoformat(),
                "end": end.isoformat(),
                "net_return": float(curve.loc[end]) / a - 1,
            }
        )
    daily = curve.loc[curve.index.hour == 0]
    rolling = {}
    for window in [7, 30, 90]:
        values = daily / daily.shift(window) - 1
        values = values.dropna()
        rolling[str(window)] = {
            "minimum": float(values.min()) if len(values) else None,
            "maximum": float(values.max()) if len(values) else None,
            "last": float(values.iloc[-1]) if len(values) else None,
            "fraction_positive": float(values.gt(0).mean()) if len(values) else None,
        }
    positions = []
    for t in trades:
        if t["close_timestamp"] == t["open_timestamp"]:
            continue
        positions.extend(
            [(t["open_timestamp"], 1, t["enter_tag"]), (t["close_timestamp"], -1, t["enter_tag"])]
        )
    open_counts = {tag: 0 for tag in sleeves}
    maximum_counts = {tag: 0 for tag in sleeves}
    # Exit before entry at the same timestamp, matching fully closed position accounting.
    for _, delta, tag in sorted(positions):
        open_counts[tag] += delta
        maximum_counts[tag] = max(maximum_counts[tag], open_counts[tag])
    if (
        any(open_counts.values())
        or maximum_counts["small_pullback"] > 2
        or maximum_counts["core_breakout"] > 4
    ):
        raise ValueError("Native sleeve slot caps did not reconcile")
    return {
        "candidate": candidate,
        "extra_cost_bps_per_side": result["extra_cost_bps_per_side"],
        "result": result,
        "sleeves": sleeves,
        "maximum_bar_boundary_positions": maximum_counts,
        "same_bar_round_trips": sum(t["open_timestamp"] == t["close_timestamp"] for t in trades),
        "position_count_resolution": "5minute boundary inventory excludes same-bar round trips",
        "monthly_marked_equity_returns": monthly,
        "rolling_daily_boundary_returns": rolling,
        "joint_assessment": joint_assessment(result, sleeves, plan),
        "native_fill_audit_passed": True,
        "maximum_fill_cash_error_usdt": error,
        "result_sha256": sha(folder / "result.json"),
        "native_zip_sha256": sha(next(folder.glob("backtest-result-*.zip"))),
        "production_eligible": False,
    }


def train(run):
    plan = read(run / "plan.json")
    if (run / "training.json").exists():
        raise ValueError("Preserve completed experiment")
    jobs = [
        (entry["candidate"], cost) for entry in plan["training_schedule"] for cost in entry["costs"]
    ]

    def job(candidate, cost):
        parent = run / "train" / candidate
        parent.mkdir(parents=True, exist_ok=True)
        folder = parent / f"cost_{cost}bps"
        if folder.exists():
            raise ValueError("Refuse to overwrite or restart a native account")
        command = [
            sys.executable,
            "-m",
            plan.get("runner_module", "research.crypto.run_candidate"),
            "--plan",
            str(run / "plan.json"),
            "--output",
            str(folder),
            "--candidate",
            candidate,
            "--phase",
            "train",
            "--cost",
            str(cost),
        ]
        with folder.with_suffix(".log").open("x") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            record = {"pid": process.pid, "command": command, "returncode": None}
            save(folder.with_suffix(".process.json"), record)
            record["returncode"] = process.wait()
            save(folder.with_suffix(".process.json"), record)
        if record["returncode"] != 0:
            raise RuntimeError(f"Native account failed: {folder}")
        metrics = account_metrics(folder, candidate, plan)
        save(folder / "objective_metrics.json", metrics)
        print(
            {
                "candidate": candidate,
                "cost": cost,
                "net_return": metrics["result"]["net_return"],
                "win_rate": metrics["result"]["win_rate"],
                "joint_passed": metrics["joint_assessment"]["passed"],
            },
            flush=True,
        )
        return metrics

    metrics, failures = [], []
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = {pool.submit(job, candidate, cost): (candidate, cost) for candidate, cost in jobs}
        for future in as_completed(pending):
            try:
                metrics.append(future.result())
            except Exception as exc:
                failures.append({"job": pending[future], "error": str(exc)})
    save(
        run / "training.json", {"accounts": metrics, "failures": failures, "orders_enabled": False}
    )
    if failures:
        raise RuntimeError("Some native accounts failed; inspect preserved logs")
    chosen = [m for m in metrics if m["candidate"] == "AutonomousDualSleeve"]
    admitted = len(chosen) == 3 and all(m["joint_assessment"]["passed"] for m in chosen)
    save(
        run / "admission.json",
        {
            "eligible": ["AutonomousDualSleeve"] if admitted else [],
            "joint_targets_passed": admitted,
            "orders_enabled": False,
        },
    )
    if not admitted:
        save(
            run / "selection.json",
            {
                "agent": None,
                "control": "cash",
                "agent_review_status": "NOT_RUN_JOINT_OBJECTIVE_FAILED",
                "future_results_included": False,
                "orders_enabled": False,
            },
        )
    print({"joint_targets_passed": admitted, "accounts_completed": len(metrics)}, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, type=Path)
    args = parser.parse_args()
    train(args.run.absolute())
