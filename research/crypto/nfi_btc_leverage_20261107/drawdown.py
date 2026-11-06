"""Explain the exact marked peak-to-trough move of the frozen 2024 2x paths."""

from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi_btc_leverage_20261107 import study
from research.crypto.nfi_solo_allocation_20261107.long_mark import grids
from research.crypto.nfi_strength_allocation_20261107 import mark_valuation, temporal
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked,
    load_module,
    save,
    sha,
)


def attribute(curve, holdings, sources, marks, oracle):
    trough = curve.drawdown.idxmax()
    peak = curve.equity.loc[:trough].idxmax()
    endpoints = pd.DatetimeIndex([peak, trough])
    timestamps = endpoints.as_unit("ms").asi8
    by_pair = defaultdict(float)
    changes = []
    prices = {}
    for trade in holdings:
        pair = trade["pair"]
        if pair not in prices:
            prices[pair] = pd.read_parquet(checked(marks[pair]["frame"])).open.loc[endpoints]
        part = np.zeros(2)
        oracle.contribute(trade, timestamps, prices[pair].to_numpy(), *sources[pair], part)
        delta = float(part[1] - part[0])
        by_pair[pair] += delta
        if delta:
            changes.append(
                {
                    "pair": pair,
                    "entry_utc": pd.to_datetime(
                        trade["open_timestamp"], unit="ms", utc=True
                    ).isoformat(),
                    "is_short": trade["is_short"],
                    "enter_tag": trade["enter_tag"].strip(),
                    "net_change_usdt": delta,
                }
            )
    high, low = (float(curve.equity.at[t]) for t in endpoints)
    if not np.isclose(sum(by_pair.values()), low - high, atol=1e-8, rtol=0):
        raise ValueError("Position effects do not reconstruct the account drawdown")
    return {
        "peak_utc": peak.isoformat(),
        "trough_utc": trough.isoformat(),
        "peak_equity_usdt": high,
        "trough_equity_usdt": low,
        "pair_net_change_usdt": {
            p: v for p, v in sorted(by_pair.items(), key=lambda row: row[1]) if v
        },
        "trade_net_change_usdt": sorted(changes, key=lambda row: row["net_change_usdt"]),
    }


def main():
    plan = study.read(study.check()["2024_Native2x"])
    result = study.read(study.RESULT)
    if result["protocol_sha256"] != sha(study.PROTOCOL):
        raise ValueError("Economic results do not match the original registration")
    stage = result["stages"]["2024"]
    hour, _ = grids(plan)
    temporal.OUT = study.OUT
    oracle = load_module("leverage_drawdown_oracle", mark_valuation.ORACLE)
    holdings, sources, checks = mark_valuation.trades_and_checks(stage, plan, oracle, hour)
    if max(checks.values()) > 1e-5:
        raise ValueError("Hourly NAV changed")
    accounts = {}
    for key, rows in holdings.items():
        curve = pd.read_parquet(checked(stage["marked_risk"][key]["curve"])).set_index("date")
        accounts[key] = attribute(curve, rows, sources, stage["mark_sources"], oracle)
    save(
        Path(__file__).with_name("drawdown_attribution.json"),
        {
            "source_sha256": sha(Path(__file__)),
            "result_sha256": sha(study.RESULT),
            "post_result_attribution": True,
            "accounts": accounts,
        },
    )


if __name__ == "__main__":
    main()
