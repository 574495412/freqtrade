"""Correct matched baseline selection and verify its actual native class and curve."""

from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi80_historical_universe_20261109 import analyze as audit
from research.crypto.nfi80_historical_universe_20261109.prepare import OUT, ROOT, read, sha
from research.crypto.nfi_strength_allocation_20261107.audit import native_trades


HERE = Path(__file__).resolve().parent


def matched_fixed(variant, cost):
    optimized = variant == "flatboost"
    directory = (
        ROOT
        / "research/crypto"
        / ("nfi80_flatboost_20261109" if optimized else "nfi80_capacity_20261109")
    )
    rows = read(directory / "comparison.json")["accounts"]
    matches = [
        r
        for r in rows
        if r["case"] == "L3_S12"
        and r["cost_bps_per_side"] == cost
        and (not optimized or r.get("variant") == "FlatTopBoost")
    ]
    if len(matches) != 1:
        raise ValueError("Baseline strategy/case/cost match is ambiguous")
    row = dict(matches[0])
    for name in ("native_archive", "native_result", "observed_equity", "cash_audit", "completion"):
        if sha(row[name]["path"]) != row[name]["sha256"]:
            raise ValueError("Baseline evidence hash differs")
    plan = read(directory / ("L3_S12-plan.json" if optimized else "L3_S12-v003.json"))
    folder = Path(row["native_result"]["path"]).parent
    strategy = "FlatTopBoost" if optimized else "NostalgiaForInfinityX8"
    if read(folder / "effective_config.json")["strategy"] != strategy:
        raise ValueError("Baseline actual strategy class differs")
    trades, cash = native_trades(plan, folder, strategy, cost)
    curve = pd.read_parquet(row["observed_equity"]["path"])
    curve = curve.loc[
        curve.date.between(pd.Timestamp(plan["start"]), pd.Timestamp(plan["comparison_end"]))
    ]
    expected = pd.date_range(plan["start"], plan["comparison_end"], freq="5min").as_unit("ns")
    if not pd.DatetimeIndex(curve.date).as_unit("ns").equals(expected):
        raise ValueError("Baseline marked calendar differs")
    nav = curve.marked_equity.to_numpy(dtype=float)
    net, dd = (nav[-1] / nav[0] - 1) * 100, np.max(1 - nav / np.maximum.accumulate(nav)) * 100
    if (
        abs(net - row["net_return_pct"]) > 1e-10
        or abs(dd - row["maximum_5m_mark_drawdown_pct"]) > 1e-10
    ):
        raise ValueError("Baseline metrics do not match its actual native account")
    row.update(
        variant=variant,
        universe="fixed",
        actual_native_strategy=strategy,
        independently_rechecked_fill_cash=cash,
        actual_native_records=len(trades),
        research_risk_gate_passed=dd <= 30 and row["native_modeled_liquidations"] == 0,
    )
    row["feasibility_passed"] = net > 0 and row["research_risk_gate_passed"]
    return row


def main():
    audit.HERE = HERE
    audit.OUT = OUT / "accounts-v003"
    audit.fixed = matched_fixed
    audit.main()


if __name__ == "__main__":
    main()
