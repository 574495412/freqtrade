"""Read-only event-ownership sensitivity on frozen native fills, not a backtest.

No quantity or strategy outcome is recomputed. Counterfactual funding differences
must not be promoted to account-return evidence: funding changes future sizing.
"""

import io
import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

from research.crypto.band_trend_native_20261113_v1.analyze import read_native
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha


HERE = Path(__file__).resolve().parent
OLD = HERE.parent / "session_reversal_native_20261113_v1"


def main():
    plan = json.loads((HERE / "plan.json").read_text())
    prior = json.loads((OLD / "funding_audit.json").read_text())
    inputs = {}
    for source in prior["records"]:
        frames = []
        for receipt in source["archives"]:
            path = Path(receipt["path"])
            if sha(path) != receipt["sha256"]:
                raise ValueError("Raw funding archive changed")
            with ZipFile(path) as archive:
                frames.append(pd.read_csv(io.BytesIO(archive.read(archive.namelist()[0]))))
        raw = pd.concat(frames, ignore_index=True)
        hours = pd.to_datetime(raw.calc_time, unit="ms", utc=True).dt.floor("h")
        mark = (
            pd.read_feather(
                Path(plan["datadir"]) / f"futures/{source['symbol']}_USDT_USDT-1h-mark.feather"
            )
            .set_index("date")
            .open
        )
        inputs[source["symbol"]] = {
            "hour_ms": pd.DatetimeIndex(hours).as_unit("ms").asi8,
            "public_ms": raw.calc_time.to_numpy(dtype=np.int64),
            "unit_fee": raw.last_funding_rate.to_numpy()
            * mark.reindex(pd.DatetimeIndex(hours)).to_numpy(),
        }
    records = []
    for candidate in plan["candidates"]:
        sym = candidate["pairs"][0].split("/")[0]
        source = inputs[sym]
        for cost in candidate["costs"]:
            name = f"{candidate['name']}_{cost}bps"
            stats, archive, _ = read_native(Path(plan["output_root"]) / name, candidate["strategy"])
            ownership = np.zeros(len(source["hour_ms"]), dtype=int)
            totals = {
                "native_inclusive": 0.0,
                "before_boundary_orders": 0.0,
                "after_boundary_orders": 0.0,
                "public_timestamp_proxy": 0.0,
            }
            errors = []
            for trade in stats["trades"]:
                opened, closed = trade["open_timestamp"], trade["close_timestamp"]
                hour, public = source["hour_ms"], source["public_ms"]
                masks = {
                    "native_inclusive": (hour >= opened) & (hour <= closed),
                    "before_boundary_orders": (hour > opened) & (hour <= closed),
                    "after_boundary_orders": (hour >= opened) & (hour < closed),
                    "public_timestamp_proxy": (public > opened) & (public <= closed),
                }
                sign = 1 if trade["is_short"] else -1
                for model, mask in masks.items():
                    value = float(sum((source["unit_fee"][mask] * trade["amount"]).tolist())) * sign
                    totals[model] += value
                    if model == "native_inclusive":
                        errors.append(abs(value - trade["funding_fees"]))
                        ownership += mask.astype(int)
            if not np.isfinite(source["unit_fee"]).all() or max(errors, default=0) > 1e-8:
                raise ValueError("Independent inclusive native funding reconstruction differs")
            native_sum = sum(t["funding_fees"] for t in stats["trades"])
            if abs(totals["native_inclusive"] - native_sum) > 1e-7:
                raise ValueError("Aggregate native funding reconstruction differs")
            dual = source["hour_ms"][ownership > 1]
            records.append(
                {
                    "account": name,
                    "native_archive_sha256": sha(archive),
                    "native_per_trade_max_reconstruction_error_usdt": max(errors, default=0),
                    "funding_sums_on_frozen_fills_usdt": totals,
                    "fixed_fill_cash_deltas_vs_native_usdt": {
                        k: v - native_sum for k, v in totals.items() if k != "native_inclusive"
                    },
                    "events_counted_for_two_lifecycles": len(dual),
                    "dual_event_nominal_clocks": [
                        str(pd.Timestamp(t, unit="ms", tz="UTC")) for t in dual
                    ],
                }
            )
    save(
        HERE / "funding_boundary_diagnostic.json",
        {
            "native_formula_reconciliation_passed": True,
            "records": records,
            "mode": "Read-only fixed-native-fill sensitivity; zero strategy runs or accounts",
            "not_a_counterfactual_account_return": True,
            "future_quantity_cash_feedback_replayed": False,
            "public_calc_timestamp_is_not_private_settlement_order": True,
            "actual_account_payments_certified": False,
        },
    )
    print(
        json.dumps(
            {
                "accounts_reconciled": len(records),
                "primary_btc_dual_events": [
                    {
                        "account": r["account"],
                        "events": r["events_counted_for_two_lifecycles"],
                        "fixed_fill_cash_deltas": r["fixed_fill_cash_deltas_vs_native_usdt"],
                    }
                    for r in records
                    if r["account"].startswith("BoundaryCarrySource_BTC")
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
