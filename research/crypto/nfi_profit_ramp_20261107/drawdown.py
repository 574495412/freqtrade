"""Attribute marked peak-to-trough losses on the observed native paths."""

from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi_profit_ramp_20261107 import marks, study
from research.crypto.nfi_strength_allocation_20261107 import mark_valuation, temporal
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked,
    load_module,
    save,
    sha,
)


def main():
    plan = study.check()
    report = study.read(study.RESULT)
    if report["protocol_sha256"] != sha(study.PROTOCOL):
        raise ValueError("Original report differs from the frozen protocol")
    admission = study.read(marks.OUT / "admission.json")
    if report["marks_admission_sha256"] != sha(marks.OUT / "admission.json"):
        raise ValueError("Mark intake changed")
    hour, _ = marks.grids(plan)
    temporal.OUT = study.OUT
    oracle = load_module("a13_drawdown_oracle", mark_valuation.ORACLE)
    trades, sources, errors = mark_valuation.trades_and_checks(report, plan, oracle, hour)
    if max(errors.values()) > 1e-5:
        raise ValueError("Original hourly reconstruction differed")
    output = {}
    for name, positions in trades.items():
        curve = pd.read_parquet(checked(report["mark_results"][name]["curve"])).set_index("date")
        trough = curve.drawdown.idxmax()
        peak = curve.loc[:trough, "equity"].idxmax()
        endpoints = pd.DatetimeIndex([peak, trough])
        timestamps = endpoints.as_unit("ms").asi8
        pnl = defaultdict(float)
        marks_at = {}
        holding_count = 0
        for trade in positions:
            pair = trade["pair"]
            if pair not in marks_at:
                path = checked(admission["marks"][pair]["frame"])
                marks_at[pair] = pd.read_parquet(path).open.loc[endpoints].to_numpy()
            funding, native_mark = sources[pair]
            part = np.zeros(2)
            oracle.contribute(trade, timestamps, marks_at[pair], funding, native_mark, part)
            pnl[pair] += float(part[1] - part[0])
            first = min(o["order_filled_timestamp"] for o in trade["orders"] if o["ft_is_entry"])
            holding_count += bool(first < timestamps[1] <= trade["close_timestamp"])
        peak_equity, trough_equity = (float(curve.equity.at[t]) for t in endpoints)
        if not np.isclose(sum(pnl.values()), trough_equity - peak_equity, atol=1e-8, rtol=0):
            raise ValueError("Drawdown attribution failed equity parity")
        output[name] = {
            "peak_utc": peak.isoformat(),
            "trough_utc": trough.isoformat(),
            "peak_equity_usdt": peak_equity,
            "trough_equity_usdt": trough_equity,
            "positions_at_trough": holding_count,
            "pair_net_change_usdt": {
                pair: value for pair, value in sorted(pnl.items(), key=lambda r: r[1]) if value
            },
        }
    save(
        Path(__file__).with_name("drawdown_attribution.json"),
        {
            "result_sha256": sha(study.RESULT),
            "source_sha256": sha(Path(__file__)),
            "post_result_attribution": True,
            "accounts": output,
        },
    )


if __name__ == "__main__":
    main()
