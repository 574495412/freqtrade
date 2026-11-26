"""Actual official loader/monthly precision comparison before any new economics."""

import gc
import hashlib
import json
import resource
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).parent.resolve()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    protocol = json.loads((HERE / "precision_protocol.json").read_text())
    if sha(__file__) != protocol["audit_code_sha256"]:
        raise ValueError("Frozen precision auditor differs")
    engine = Path(protocol["engine_source"])
    sys.path.insert(0, str(engine))
    from freqtrade.configuration import TimeRange
    from freqtrade.data.btanalysis.historic_precision import get_tick_size_over_time
    from freqtrade.data.history import load_data
    from freqtrade.enums import CandleType

    for path, digest in protocol["native_owners"].items():
        if sha(path) != digest:
            raise ValueError("Official precision owner differs")
    snapshot = json.loads(Path(protocol["market_snapshot"]["path"]).read_text())
    if sha(protocol["market_snapshot"]["path"]) != protocol["market_snapshot"]["sha256"]:
        raise ValueError("Frozen quantity/funding/price snapshot differs")
    grid = pd.date_range(protocol["comparison_start"], protocol["comparison_end"], freq="h")
    summaries, months, decisions = [], [], []
    for pair in protocol["pairs"]:
        series = {}
        loaded_rows = {}
        for label, setup in protocol["routes"].items():
            frame = load_data(
                datadir=Path(protocol["datadir"]),
                pairs=[pair],
                timeframe=setup["timeframe"],
                timerange=TimeRange.parse_timerange(setup["timerange"]),
                startup_candles=setup["startup_candles"],
                fail_without_data=True,
                data_format="feather",
                candle_type=CandleType.FUTURES,
            )[pair]
            loaded_rows[label] = len(frame)
            series[label] = get_tick_size_over_time(frame)
            del frame
        joined = pd.concat(
            [series["nfi5m"].rename("nfi_tick"), series["hs1h"].rename("hs_tick")], axis=1
        )
        for month, row in joined.iterrows():
            months.append(
                {
                    "pair": pair,
                    "month": month,
                    "nfi_tick": row.nfi_tick,
                    "hs_tick": row.hs_tick,
                    "exact_equal": bool(row.nfi_tick == row.hs_tick),
                }
            )
        fallback = snapshot["markets"][pair]["precision"]["price"]
        nfi = series["nfi5m"].asof(grid).fillna(fallback).to_numpy()
        hs = series["hs1h"].asof(grid).fillna(fallback).to_numpy()
        mismatch = nfi != hs
        decision = pd.DataFrame(
            {"pair": pair, "date": grid, "nfi_tick": nfi, "hs_tick": hs, "exact_equal": ~mismatch}
        )
        decisions.append(decision)
        summaries.append(
            {
                "pair": pair,
                "loaded_rows": loaded_rows,
                "months_compared": len(joined),
                "monthly_table_mismatches": int((joined.nfi_tick != joined.hs_tick).sum()),
                "common_hourly_decisions": len(grid),
                "common_hourly_tick_mismatches": int(mismatch.sum()),
            }
        )
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss = peak if sys.platform == "darwin" else peak * 1024
        if rss > protocol["maximum_rss_bytes"]:
            raise RuntimeError("Precision comparison RSS guard exceeded")
        print(json.dumps(summaries[-1]), flush=True)
        gc.collect()
    monthly = pd.DataFrame(months)
    all_decisions = pd.concat(decisions, ignore_index=True)
    monthly.to_parquet(HERE / "precision_monthly_comparison.parquet", index=False)
    all_decisions.to_parquet(HERE / "precision_hourly_comparison.parquet", index=False)
    # This exact 5m native table is a conditional legacy reference, never a claim
    # about actual historically published exchange ticks.
    monthly[["pair", "month", "nfi_tick"]].to_parquet(
        HERE / "legacy5m_reference.parquet", index=False
    )
    table_differences = sum(row["monthly_table_mismatches"] for row in summaries)
    hourly_differences = sum(row["common_hourly_tick_mismatches"] for row in summaries)
    receipt = {
        "completed_at": datetime.now(UTC).isoformat(),
        "diagnostic_completed": True,
        "default_route_exactly_equal": table_differences == 0 and hourly_differences == 0,
        "native_monthly_table_mismatches": table_differences,
        "common_hourly_tick_mismatches": hourly_differences,
        "pairs": len(summaries),
        "common_hourly_decisions": len(all_decisions),
        "records": summaries,
        "protocol_sha256": sha(HERE / "precision_protocol.json"),
        "source_1h_signals_unchanged": True,
        "actual_historical_rules_certified": False,
        "native_lookahead_corrected": False,
        "financial_start_called": False,
        "required_next_route": "UNCHANGED_DEFAULT"
        if table_differences == 0 and hourly_differences == 0
        else "REGISTER_SHARED_LEGACY_5M_REFERENCE_BEFORE_FINANCE",
        "artifacts": {
            name: {"path": str(HERE / name), "sha256": sha(HERE / name)}
            for name in [
                "precision_monthly_comparison.parquet",
                "precision_hourly_comparison.parquet",
                "legacy5m_reference.parquet",
            ]
        },
    }
    with (HERE / "precision_route_validation.json").open("x") as stream:
        json.dump(receipt, stream, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "table_mismatches": table_differences,
                "hourly_mismatches": hourly_differences,
                "required_next_route": receipt["required_next_route"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
