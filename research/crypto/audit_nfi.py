"""Derive explicit economic metrics from native execution receipts without changing a run."""

import argparse
import json
from pathlib import Path
from zipfile import ZipFile

from freqtrade.research.data import read, save, sha


def native_stats(folder: Path) -> dict:
    archives = list(folder.glob("backtest-result-*.zip"))
    if len(archives) != 1:
        raise ValueError("Expected one immutable native trade archive")
    with ZipFile(archives[0]) as archive:
        files = [
            name
            for name in archive.namelist()
            if name.endswith(".json") and not name.endswith("_config.json")
        ]
        if len(files) != 1:
            raise ValueError("Ambiguous native results")
        return json.loads(archive.read(files[0]))["strategy"]["NostalgiaForInfinityX7"]


def audit(folder: Path) -> dict:
    result = read(folder / "result.json")
    stats = native_stats(folder)
    positive = sum(max(trade["profit_abs"], 0) for trade in stats["trades"])
    negative = -sum(min(trade["profit_abs"], 0) for trade in stats["trades"])
    if abs(positive - negative - result["net_profit_usdt"]) > 1e-5:
        raise ValueError("Native full-trade totals did not reconcile")
    # Avoid native's 0-PF convention when every completed trade won. Test the ratio as
    # gross_profit >= target * gross_loss, equivalent without dividing by zero.
    plan = read(folder / "plan.json")
    targets = plan["screening_targets"]
    reasons = []
    if result["closed_trades"] < targets["minimum_closed_trades_per_evaluation"]:
        reasons.append("完整交易不足")
    if result["net_return"] <= 0:
        reasons.append("净收益非正")
    if positive < targets["minimum_profit_factor"] * negative or positive == 0:
        reasons.append("利润因子不足")
    if result["risk_drawdown_for_screening"] > targets["maximum_observed_drawdown"]:
        reasons.append("含浮亏观察或原生回撤超标")
    return {
        "schema": "crypto-quant-nfi-audit.v1",
        "native_result_sha256": sha(folder / "result.json"),
        "gross_profit_usdt": positive,
        "gross_loss_usdt": negative,
        "profit_factor": positive / negative if negative else None,
        "profit_factor_status": "FINITE" if negative else "NO_LOSSES_OBSERVED",
        "profit_factor_gate_passed": positive > 0
        and positive >= targets["minimum_profit_factor"] * negative,
        "phase_economic_screen_passed": not reasons,
        "reasons": reasons,
        "phase": result["phase"],
        "variant": result["variant"],
        "extra_cost_bps_per_side": result["extra_cost_bps_per_side"],
        "orders_enabled": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    args = parser.parse_args()
    target = args.folder / "economic_audit.json"
    if target.exists():
        raise ValueError("Audit already exists; do not overwrite evidence")
    result = audit(args.folder.resolve())
    save(target, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
