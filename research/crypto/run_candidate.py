"""Run a registered strategy with the native account and immutable input identities."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from math import isfinite
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

from freqtrade.commands.optimize_commands import setup_optimize_configuration
from freqtrade.enums import RunMode
from freqtrade.persistence import LocalTrade
from freqtrade.research.data import read, save, sha
from freqtrade.research.engine import configuration
from freqtrade.research.workflow import source_identity
from research.crypto.run_nfi import ObservedBacktesting


def native_stats(folder: Path) -> dict:
    archives = list(folder.glob("backtest-result-*.zip"))
    if len(archives) != 1:
        raise ValueError("Expected one native trade archive")
    with ZipFile(archives[0]) as archive:
        files = [
            name
            for name in archive.namelist()
            if name.endswith(".json") and not name.endswith("_config.json")
        ]
        if len(files) != 1:
            raise ValueError("Ambiguous native results")
        strategies = json.loads(archive.read(files[0]))["strategy"]
    if len(strategies) != 1:
        raise ValueError("One account must contain one candidate")
    return next(iter(strategies.values()))


def economic_audit(stats, plan, observed_drawdown):
    if not all(
        isfinite(x)
        for x in [
            stats["profit_total"],
            stats["profit_total_abs"],
            stats["max_drawdown_account"],
            observed_drawdown,
            *[t["profit_abs"] for t in stats["trades"]],
        ]
    ):
        raise ValueError("Nonfinite economic result")
    positive = sum(max(t["profit_abs"], 0) for t in stats["trades"])
    negative = -sum(min(t["profit_abs"], 0) for t in stats["trades"])
    if abs(positive - negative - stats["profit_total_abs"]) > 1e-5:
        raise ValueError("Native cash totals did not reconcile")
    target = plan["screening_targets"]
    risk_dd = max(observed_drawdown, stats["max_drawdown_account"])
    reasons = []
    if stats["total_trades"] < target["minimum_closed_trades_per_evaluation"]:
        reasons.append("完整交易不足")
    if stats["profit_total"] <= 0:
        reasons.append("净收益非正")
    if positive == 0 or positive < target["minimum_profit_factor"] * negative:
        reasons.append("利润因子不足")
    if risk_dd > target["maximum_observed_drawdown"]:
        reasons.append("含浮亏观察或原生回撤超标")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "gross_profit": positive,
        "gross_loss": negative,
        "profit_factor": positive / negative if negative else None,
        "profit_factor_status": "FINITE" if negative else "NO_LOSSES_OBSERVED",
        "risk_drawdown": risk_dd,
    }


def run(plan_path: Path, output: Path, candidate_name: str, phase: str, cost: int):
    plan = read(plan_path)
    candidate = next(c for c in plan["candidates"] if c["name"] == candidate_name)
    dataset = Path(plan["dataset"])
    if sha(dataset / "receipts.json") != plan["data_receipts_sha256"]:
        raise ValueError("Dataset identity mismatch")
    for name, expected in read(dataset / "receipts.json").items():
        if sha(dataset / name) != expected:
            raise ValueError(f"Dataset file changed: {name}")
    if any(k.startswith("FREQTRADE__") for k in os.environ):
        raise ValueError("Unexpected configuration environment override")
    output.mkdir(parents=True, exist_ok=False)
    strategy_dir = output / "strategies"
    strategy_dir.mkdir()
    for record in plan["strategy_sources"]:
        path = Path(record["path"])
        if sha(path) != record["sha256"]:
            raise ValueError(f"Registered source changed: {path}")
        shutil.copyfile(path, strategy_dir / path.name)
    start, end = (
        (plan["validation_start"], plan["holdout_end"])
        if phase == "continuous"
        else (
            (plan["train_start"], "2024-02-01")
            if phase == "compatibility"
            else (plan[f"{phase}_start"], plan[f"{phase}_end"])
        )
    )
    adapted = dict(
        plan,
        candidates=[candidate_name],
        trading_mode="futures",
        margin_mode="isolated",
        exchange="binance",
        stake_usdt=plan["stake_amount"],
    )
    raw = configuration(adapted, dataset, output, start, end, cost)
    raw.pop("strategy_list")
    raw.update(strategy_path=str(strategy_dir), **plan.get("settings", {}))
    save(output / "settings.json", raw)
    save(output / "plan.json", plan)
    save(
        output / "freeze.json",
        {
            "plan_sha256": sha(plan_path),
            "source_identity": source_identity(),
            "runner_sha256": sha(Path(__file__)),
            "observer_sha256": sha(Path(__file__).with_name("run_nfi.py")),
            "phase": phase,
            "candidate": candidate_name,
            "cost": cost,
            "orders_enabled": False,
        },
    )
    (output / "requirements.lock.txt").write_text(
        subprocess.run(
            [sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True, check=True
        ).stdout
    )
    config = setup_optimize_configuration(
        {"config": [str(output / "settings.json")], "datadir": str(dataset / "candles")},
        RunMode.BACKTEST,
    )
    engine = None
    try:
        engine = ObservedBacktesting(config, dataset)
        engine.progress = None
        engine.start()
        stats = engine.results["strategy"][candidate_name]
        if any(t["leverage"] not in candidate["allowed_leverage"] for t in stats["trades"]):
            raise ValueError("Actual leverage violates registered strategy")
        if bool(engine.strategy.can_short) != candidate["can_short"]:
            raise ValueError("Actual directional capability differs from the protocol")
        if (
            LocalTrade.bt_trades_open
            or abs(LocalTrade.bt_total_profit - stats["profit_total_abs"]) > 1e-5
        ):
            raise ValueError("Final native account did not reconcile")
        equity = pd.DataFrame(engine.observations, columns=["date", "equity", "open_trades"])
        equity.loc[len(equity)] = [
            pd.Timestamp(end, tz="UTC"),
            plan["initial_equity_usdt"] + stats["profit_total_abs"],
            0,
        ]
        peaks = np.maximum.accumulate(np.r_[plan["initial_equity_usdt"], equity.equity])[1:]
        equity["drawdown"] = 1 - equity.equity / peaks
        equity.to_parquet(output / "hourly_equity.parquet", index=False)
        audit = economic_audit(stats, plan, float(equity.drawdown.max()))
        result = {
            "candidate": candidate_name,
            "phase": phase,
            "start": start,
            "end": end,
            "extra_cost_bps_per_side": cost,
            "net_return": stats["profit_total"],
            "net_profit_usdt": stats["profit_total_abs"],
            "closed_trades": stats["total_trades"],
            "win_rate": stats["wins"] / stats["total_trades"] if stats["total_trades"] else None,
            "native_closed_drawdown": stats["max_drawdown_account"],
            "hourly_observed_drawdown": float(equity.drawdown.max()),
            "economic_audit": audit,
            "funding_net_usdt": sum(t["funding_fees"] for t in stats["trades"]),
            "actual_leverage_values": sorted({t["leverage"] for t in stats["trades"]}),
            "can_short": engine.strategy.can_short,
            "native_final_account_reconciled": True,
            "historical_evidence_status": plan["historical_evidence_status"],
            "orders_enabled": False,
            "production_eligible": False,
        }
        save(output / "result.json", result)
        return result
    finally:
        if engine is not None:
            engine.exchange.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument(
        "--phase",
        choices=["compatibility", "train", "validation", "holdout", "continuous"],
        required=True,
    )
    parser.add_argument("--cost", type=int, choices=[0, 3, 10], required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.plan.resolve(), args.output.resolve(), args.candidate, args.phase, args.cost),
            indent=2,
        )
    )
