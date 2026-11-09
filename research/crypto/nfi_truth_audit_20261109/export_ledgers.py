"""Expose actual native records and reconcile their complete marked equity."""

import json
import math
import shutil
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

from research.crypto.nfi_strength_allocation_20261107.audit import native_trades


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = ROOT / "user_data/crypto_quant/nfi_truth_audit_20261109_v001/ledgers"


def read(p):
    return json.loads(Path(p).read_text())


def save(p, value):
    Path(p).write_text(json.dumps(value, indent=2, default=str) + "\n")


def main():
    OUT.mkdir(exist_ok=False)
    source = ROOT / "research/crypto/nfi80_historical_universe_20261109/v003"
    plan = read(source / "flatboost-plan.json")
    rows = [
        r
        for r in read(source / "comparison.json")["accounts"]
        if r["variant"] == "flatboost" and r["universe"] == "daily"
    ]
    reports = []
    for row in rows:
        cost = row["cost_bps_per_side"]
        folder = Path(row["native_result"]["path"]).parent
        _trades, cash = native_trades(plan, folder, "FlatTopBoost", cost)
        (archive,) = (folder / "native_results").glob("*.zip")
        with ZipFile(archive) as z:
            name = next(
                n for n in z.namelist() if n.endswith(".json") and not n.endswith("_config.json")
            )
            (OUT / f"native-{cost}bps.json").write_bytes(z.read(name))
        curve = pd.read_parquet(folder / "five_minute_equity.parquet")
        curve = curve.loc[
            curve.date.between(pd.Timestamp(plan["start"]), pd.Timestamp(plan["comparison_end"]))
        ]
        expected = pd.date_range(plan["start"], plan["comparison_end"], freq="5min").as_unit("ns")
        if not pd.DatetimeIndex(curve.date).as_unit("ns").equals(expected):
            raise ValueError("Complete native five-minute calendar missing")
        nav = curve.marked_equity.to_numpy(dtype=float)
        ret, dd = (nav[-1] / nav[0] - 1) * 100, np.max(1 - nav / np.maximum.accumulate(nav)) * 100
        if (
            abs(ret - row["net_return_pct"]) > 1e-10
            or abs(dd - row["maximum_5m_mark_drawdown_pct"]) > 1e-10
        ):
            raise ValueError("Reported return/drawdown do not match actual marked curve")
        curve.to_csv(OUT / f"equity-{cost}bps.csv", index=False)
        shutil.copy2(folder / "five_minute_equity.parquet", OUT / f"equity-{cost}bps.parquet")
        native = json.loads((OUT / f"native-{cost}bps.json").read_text())["strategy"][
            "FlatTopBoost"
        ]["trades"]
        pd.DataFrame([{k: v for k, v in t.items() if k != "orders"} for t in native]).to_csv(
            OUT / f"campaigns-{cost}bps.csv", index=False
        )
        fills = [
            {"campaign_index": i, "pair": t["pair"], "campaign_open_utc": t["open_date"], **o}
            for i, t in enumerate(native)
            for o in t["orders"]
        ]
        pd.DataFrame(fills).to_csv(OUT / f"fills-{cost}bps.csv", index=False)
        natural = [t for t in native if t["exit_reason"] != "force_exit"]
        force = [t for t in native if t["exit_reason"] == "force_exit"]
        gain = [t["profit_abs"] for t in natural if t["profit_abs"] > 0]
        loss = [t["profit_abs"] for t in natural if t["profit_abs"] < 0]
        months = []
        starts = list(
            pd.date_range(
                pd.Timestamp(plan["start"]), pd.Timestamp(plan["comparison_end"]), freq="MS"
            )
        )
        for start, end in zip(
            starts, [*starts[1:], pd.Timestamp(plan["comparison_end"])], strict=True
        ):
            values = curve.loc[curve.date.between(start, end), "marked_equity"].to_numpy()
            months.append(
                {
                    "month": str(start.date()),
                    "end": str(end),
                    "net_return_pct": (values[-1] / values[0] - 1) * 100,
                    "maximum_5m_drawdown_pct": np.max(1 - values / np.maximum.accumulate(values))
                    * 100,
                }
            )
        if abs(math.prod(1 + r["net_return_pct"] / 100 for r in months) - nav[-1] / nav[0]) > 1e-12:
            raise ValueError("Monthly native curve returns do not compound correctly")
        pd.DataFrame(months).to_csv(OUT / f"monthly-{cost}bps.csv", index=False)
        reports.append(
            {
                "cost_bps_per_side": cost,
                "net_return_pct": ret,
                "maximum_5m_mark_drawdown_pct": dd,
                "native_records": len(native),
                "natural_completed_campaigns": len(natural),
                "natural_wins": len(gain),
                "natural_losses": len(loss),
                "natural_win_rate_pct": len(gain) / len(natural) * 100,
                "terminal_force_exits": len(force),
                "terminal_force_exit_pnl_usdt": sum(t["profit_abs"] for t in force),
                "win_rate_including_terminal_force_exits_pct": sum(
                    t["profit_abs"] > 0 for t in native
                )
                / len(native)
                * 100,
                "average_natural_win_usdt": np.mean(gain),
                "average_natural_loss_usdt": np.mean(loss),
                "natural_win_loss_payoff_ratio": np.mean(gain) / -np.mean(loss),
                "closed_natural_pnl_usdt": sum(t["profit_abs"] for t in natural),
                "closed_realized_pnl_at_cutoff_usdt": curve.closed_realized_pnl.iloc[-1],
                "open_mark_pnl_after_close_fee_at_cutoff_usdt": nav[-1]
                - 1000
                - curve.closed_realized_pnl.iloc[-1],
                "worst_natural_campaign_pnl_usdt": min(t["profit_abs"] for t in natural),
                "actual_addition_fills": row["actual_addition_fills"],
                "max_entries_per_trade": row["max_entries_per_trade"],
                "cash_reconciliation": cash,
                "calendar_points": len(curve),
                "hindsight_blacklist_bias": True,
                "independent_oos": False,
                "periods": months,
            }
        )
    save(
        HERE / "ledger-audit.json",
        {
            "accounts": reports,
            "raw_exports": str(OUT),
            "scope": (
                "Actual native artifacts; retrospective blacklist and survivor/source selection "
                "remain. Reconciliation does not cure those biases."
            ),
            "orders_enabled": False,
        },
    )
    print(
        json.dumps(
            [
                {
                    k: r[k]
                    for k in (
                        "cost_bps_per_side",
                        "net_return_pct",
                        "natural_win_rate_pct",
                        "average_natural_win_usdt",
                        "average_natural_loss_usdt",
                        "open_mark_pnl_after_close_fee_at_cutoff_usdt",
                    )
                }
                for r in reports
            ]
        )
    )


if __name__ == "__main__":
    main()
