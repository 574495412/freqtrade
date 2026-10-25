"""Run immutable external NFI strategies through the native Freqtrade account."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.commands.optimize_commands import setup_optimize_configuration
from freqtrade.enums import RunMode
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.persistence import LocalTrade
from freqtrade.research.data import read, save, sha
from freqtrade.research.engine import configuration
from freqtrade.research.workflow import source_identity


def open_trade_value(trade: LocalTrade, mark: float) -> float:
    """Native remaining-size PnL plus partial realizations, without funding counted twice."""
    filled = [order for order in trade.orders if not order.ft_is_open and order.filled]
    exits = [index for index, order in enumerate(filled) if order.ft_order_side != trade.entry_side]
    # Native recalc allocates all accrued funding up to a partial exit to its realized PnL.
    realized_funding = (
        sum(order.funding_fee or 0.0 for order in filled[: exits[-1] + 1]) if exits else 0.0
    )
    return trade.calculate_profit(mark).total_profit - realized_funding


class ObservedBacktesting(Backtesting):
    """Read-only hourly valuation; order matching and position updates remain native."""

    def __init__(self, config: dict, dataset: Path):
        self.observations = []
        self.marks = {}
        for pair in config["exchange"]["pair_whitelist"]:
            name = pair.replace("/", "_").replace(":", "_")
            frame = pd.read_feather(dataset / f"candles/futures/{name}-1h-mark.feather")
            self.marks[pair] = frame.set_index("date")["open"]
        super().__init__(config)

    def _capture_wallet(self, current_time, currency: str, price: float) -> None:
        super()._capture_wallet(current_time, currency, price)
        if currency != self.config["stake_currency"] or current_time.minute != 0:
            return
        value = float(self.config["dry_run_wallet"]) + LocalTrade.bt_total_profit
        for trade in LocalTrade.bt_trades_open:
            value += open_trade_value(trade, float(self.marks[trade.pair].loc[current_time]))
        self.observations.append((current_time, value, len(LocalTrade.bt_trades_open)))


def settings(plan, variant, dataset, folder, start, end, cost):
    adapted = dict(
        plan,
        stake_usdt="unlimited",
        trading_mode="futures",
        margin_mode="isolated",
        exchange="binance",
        candidates=["NostalgiaForInfinityX7"],
    )
    config = configuration(adapted, dataset, folder, start, end, cost)
    config.pop("strategy_list")
    config.update(
        strategy_path=str(folder / "strategies"),
        nfi_parameters=variant["nfi_parameters"],
        use_exit_signal=True,
        exit_profit_only=False,
        ignore_roi_if_entry_signal=True,
    )
    # NFI keeps its native limit orders, management and stop thresholds.
    return config


def verify_inputs(plan, dataset, source):
    if read(dataset / "plan.json") != plan:
        raise ValueError("NFI dataset protocol mismatch")
    for relative, expected in read(dataset / "receipts.json").items():
        if sha(dataset / relative) != expected:
            raise ValueError(f"Dataset changed: {relative}")
    if sha(source) != plan["source_sha256"]:
        raise ValueError("External source revision mismatch")
    if any(key.startswith("FREQTRADE__") for key in os.environ):
        raise ValueError("Unexpected configuration environment overrides")


def run(plan, dataset, source, output, variant_name, phase, cost):
    verify_inputs(plan, dataset, source)
    variant = next(v for v in plan["variants"] if v["name"] == variant_name)
    if phase == "compatibility":
        start, end = plan["train_start"], "2024-02-01"
    elif phase == "continuous":
        start, end = plan["validation_start"], plan["holdout_end"]
    else:
        start, end = plan[f"{phase}_start"], plan[f"{phase}_end"]
    output.mkdir(parents=True, exist_ok=False)
    (output / "strategies").mkdir()
    shutil.copyfile(source, output / "strategies" / source.name)
    save(output / "plan.json", plan)
    save(
        output / "freeze.json",
        {
            "source_identity": source_identity(),
            "external_source_sha256": sha(source),
            "runner_sha256": sha(Path(__file__)),
            "plan_sha256": sha(output / "plan.json"),
            "data_receipts_sha256": sha(dataset / "receipts.json"),
            "variant": variant,
            "phase": phase,
            "cost": cost,
            "start": start,
            "end": end,
            "orders_enabled": False,
        },
    )
    (output / "requirements.lock.txt").write_text(
        subprocess.run(
            [sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True, check=True
        ).stdout
    )
    raw = settings(plan, variant, dataset, output, start, end, cost)
    save(output / "settings.json", raw)
    config = setup_optimize_configuration(
        {"config": [str(output / "settings.json")], "datadir": str(dataset / "candles")},
        RunMode.BACKTEST,
    )
    engine = None
    try:
        engine = ObservedBacktesting(config, dataset)
        engine.progress = None
        engine.start()
        stats = engine.results["strategy"]["NostalgiaForInfinityX7"]
        if any(trade["leverage"] != variant["leverage"] for trade in stats["trades"]):
            raise ValueError("Native strategy leverage differs from the frozen variant")
        observations = pd.DataFrame(engine.observations, columns=["date", "equity", "open_trades"])
        final_equity = plan["initial_equity_usdt"] + stats["profit_total_abs"]
        observations.loc[len(observations)] = [pd.Timestamp(end, tz="UTC"), final_equity, 0]
        peaks = np.maximum.accumulate(np.r_[plan["initial_equity_usdt"], observations.equity])[1:]
        observations["drawdown"] = 1 - observations.equity / peaks
        if not np.isfinite(observations[["equity", "drawdown"]].to_numpy()).all():
            raise ValueError("Nonfinite observed equity")
        observations.to_parquet(output / "hourly_equity.parquet", index=False)
        # Read-only observations must reconcile with the terminal native account.
        if (
            LocalTrade.bt_trades_open
            or abs(LocalTrade.bt_total_profit - stats["profit_total_abs"]) > 1e-5
        ):
            raise ValueError("Native final account did not reconcile")
        result = {
            "schema": "crypto-quant-nfi-phase.v1",
            "variant": variant_name,
            "phase": phase,
            "start": start,
            "end": end,
            "extra_cost_bps_per_side": cost,
            "net_return": stats["profit_total"],
            "net_profit_usdt": stats["profit_total_abs"],
            "closed_trades": stats["total_trades"],
            "profit_factor": stats["profit_factor"],
            "win_rate": stats["wins"] / stats["total_trades"] if stats["total_trades"] else None,
            "native_closed_trade_drawdown": stats["max_drawdown_account"],
            "hourly_observed_drawdown": float(observations.drawdown.max()),
            "risk_drawdown_for_screening": max(
                float(observations.drawdown.max()), stats["max_drawdown_account"]
            ),
            "native_final_account_reconciled": True,
            "funding_net_usdt": sum(t["funding_fees"] for t in stats["trades"]),
            "leverage": variant["leverage"],
            "position_adjustment_enabled": engine.strategy.position_adjustment_enable,
            "exit_reasons": stats["exit_reason_summary"],
            "drawdown_convention": (
                "hourly mark open before current bar orders and funding processing; "
                "native close fee reserve; not intrahour worst case"
            ),
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
    parser.add_argument("--plan", type=Path, default=Path("research/crypto/nfi_plan.json"))
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--variant", choices=["NFIReference3x", "NFILeverage1x"], required=True)
    parser.add_argument(
        "--phase",
        choices=["compatibility", "train", "validation", "holdout", "continuous"],
        required=True,
    )
    parser.add_argument("--cost", type=int, choices=[0, 3, 10], required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                read(args.plan),
                args.dataset.resolve(),
                args.source.resolve(),
                args.output.resolve(),
                args.variant,
                args.phase,
                args.cost,
            ),
            indent=2,
        )
    )
