"""Value a fixed portfolio of complete, independently funded native accounts."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from freqtrade.research.workflow import source_identity
from research.crypto.run_candidate import economic_audit, native_stats


def combine_accounts(accounts, plan):
    """Sum synchronous NAVs and every full trade; do not scale or select component trades."""
    if len({account["name"] for account in accounts}) != len(accounts):
        raise ValueError("The same native account cannot be counted twice")
    first = accounts[0]["result"]
    start, end = first["start"], first["end"]
    clocks = pd.date_range(
        pd.Timestamp(start, tz="UTC") + pd.Timedelta(hours=1),
        pd.Timestamp(end, tz="UTC"),
        freq="h",
        tz="UTC",
    )
    equity = pd.DataFrame({"date": clocks, "equity": 0.0, "open_trades": 0})
    trades, components = [], []
    initial = sum(account["initial"] for account in accounts)
    if initial != plan["initial_equity_usdt"]:
        raise ValueError("Actual component capital differs from portfolio capital")
    for account in accounts:
        row, curve, stats = account["result"], account["equity"], account["stats"]
        if any(
            row[key] != first[key] for key in ("phase", "start", "end", "extra_cost_bps_per_side")
        ):
            raise ValueError("Component evaluation periods or costs differ")
        if not pd.DatetimeIndex(curve.date).as_unit("ns").equals(clocks.as_unit("ns")):
            raise ValueError("Component hourly clocks are incomplete or misaligned")
        if not row["native_final_account_reconciled"] or any(t["is_open"] for t in stats["trades"]):
            raise ValueError("Native component did not finish closed and reconciled")
        if abs(curve.equity.iloc[-1] - account["initial"] - row["net_profit_usdt"]) > 1e-5:
            raise ValueError("Component terminal NAV and native cash differ")
        if abs(stats["profit_total_abs"] - row["net_profit_usdt"]) > 1e-5:
            raise ValueError("Component native archive and summary differ")
        equity["equity"] += curve.equity.to_numpy()
        equity["open_trades"] += curve.open_trades.to_numpy()
        equity[account["name"] + "_equity"] = curve.equity.to_numpy()
        trades.extend(
            dict(trade, component=account["name"], native_trade_index=index)
            for index, trade in enumerate(stats["trades"])
        )
        components.append(
            dict(row, component=account["name"], initial_equity_usdt=account["initial"])
        )
    if (
        not np.isfinite(equity.equity).all()
        or equity.open_trades.max() > plan["maximum_open_positions"]
    ):
        raise ValueError("Portfolio NAV or position ceiling is invalid")
    initial_row = {column: 0 for column in equity.columns}
    initial_row.update(date=pd.Timestamp(start, tz="UTC"), equity=initial)
    for account in accounts:
        initial_row[account["name"] + "_equity"] = account["initial"]
    equity = pd.concat([pd.DataFrame([initial_row]), equity], ignore_index=True)
    equity["drawdown"] = 1 - equity.equity / equity.equity.cummax()
    closed = (
        pd.DataFrame(trades).groupby("close_timestamp").profit_abs.sum().sort_index()
        if trades
        else pd.Series(dtype=float)
    )
    closed_cash = np.r_[initial, initial + closed.cumsum().to_numpy()]
    closed_dd = float((1 - closed_cash / np.maximum.accumulate(closed_cash)).max())
    profit = sum(t["profit_abs"] for t in trades)
    if abs(equity.equity.iloc[-1] - initial - profit) > 1e-5:
        raise ValueError("Portfolio terminal native trade cash did not reconcile")
    portfolio_stats = {
        "profit_total": profit / initial,
        "profit_total_abs": profit,
        "max_drawdown_account": closed_dd,
        "total_trades": len(trades),
        "trades": trades,
    }
    audit = economic_audit(portfolio_stats, plan, float(equity.drawdown.max()))
    result = {
        "candidate": plan["candidate"],
        "phase": first["phase"],
        "start": start,
        "end": end,
        "extra_cost_bps_per_side": first["extra_cost_bps_per_side"],
        "initial_equity_usdt": initial,
        "native_account_count": len(accounts),
        "net_profit_usdt": profit,
        "net_return": profit / initial,
        "closed_trades": len(trades),
        "win_rate": sum(t["profit_abs"] > 0 for t in trades) / len(trades) if trades else None,
        "hourly_observed_drawdown": float(equity.drawdown.max()),
        "closed_cashflow_drawdown": closed_dd,
        "maximum_observed_open_positions": int(equity.open_trades.max()),
        "economic_audit": audit,
        "funding_net_usdt": sum(t["funding_fees"] for t in trades),
        "components": components,
        "capital_rebalanced": False,
        "trades_selected_or_rescaled": False,
        "independent_oos": False,
        "orders_enabled": False,
        "production_eligible": False,
    }
    return result, equity, trades


def load_account(component, phase, cost):
    run = Path(component["run"])
    if sha(run / "plan.json") != component["plan_sha256"]:
        raise ValueError("Component protocol changed")
    plan = read(run / "plan.json")
    folder = run / phase / component["name"] / f"cost_{cost}bps"
    prefix = str(folder.relative_to(run)) + "/"
    for name, expected in component["file_identities"].items():
        if name.startswith(prefix) and sha(run / name) != expected:
            raise ValueError("A complete native component receipt changed")
    if read(folder / "freeze.json")["source_identity"] != source_identity():
        raise ValueError("Native runtime changed after the component studies")
    for source in plan["strategy_sources"]:
        if sha(folder / "strategies" / Path(source["path"]).name) != source["sha256"]:
            raise ValueError("Frozen component strategy changed")
    return {
        "name": component["name"],
        "initial": component["initial_equity_usdt"],
        "result": read(folder / "result.json"),
        "stats": native_stats(folder),
        "equity": pd.read_parquet(folder / "hourly_equity.parquet"),
    }


def build(run, stage):
    plan = read(run / "plan.json")
    if stage == "train":
        if (run / "freeze.json").exists():
            raise ValueError("Portfolio training already attempted")
        save(
            run / "freeze.json",
            {
                "plan_sha256": sha(run / "plan.json"),
                "builder_sha256": sha(Path(__file__)),
                "native_source_identity": source_identity(),
            },
        )
        (run / "builder_snapshot.py").write_bytes(Path(__file__).read_bytes())
        phases = ["train"]
    else:
        frozen = read(run / "freeze.json")
        if frozen["plan_sha256"] != sha(run / "plan.json") or frozen["builder_sha256"] != sha(
            Path(__file__)
        ):
            raise ValueError("Portfolio protocol or implementation changed")
        selection = read(run / "selection.json")
        if selection["future_results_included"] is not False:
            raise ValueError("Training-only review evidence was not preserved")
        phases = ["validation", "holdout", "continuous"]
    rows = []
    for phase in phases:
        for cost in plan["extra_cost_bps_per_side"]:
            folder = run / phase / plan["candidate"] / f"cost_{cost}bps"
            folder.mkdir(parents=True, exist_ok=False)
            accounts = [load_account(c, phase, cost) for c in plan["components"]]
            result, equity, trades = combine_accounts(accounts, plan)
            equity.to_parquet(folder / "hourly_equity.parquet", index=False)
            save(folder / "trade_ledger.json", trades)
            save(folder / "result.json", result)
            rows.append(result)
            print(
                {
                    k: result[k]
                    for k in ("phase", "extra_cost_bps_per_side", "net_return", "economic_audit")
                },
                flush=True,
            )
    if stage == "train":
        save(run / "training.json", rows)
    else:
        all_rows = read(run / "training.json") + rows
        save(
            run / "evaluation.json",
            {
                "candidate": plan["candidate"],
                "all_historical_economic_screens_passed": all(
                    r["economic_audit"]["passed"] for r in all_rows
                ),
                "rows": all_rows,
                "all_component_future_outcomes_already_observed": True,
                "independent_oos": False,
                "orders_enabled": False,
                "production_eligible": False,
            },
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--stage", choices=["train", "future"], required=True)
    args = parser.parse_args()
    build(args.run.resolve(), args.stage)
