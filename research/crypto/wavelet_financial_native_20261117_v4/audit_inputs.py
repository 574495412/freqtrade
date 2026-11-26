"""Independent native-input continuity, aggregation and valuation-mark checks."""

# ruff: noqa: S101
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    output = HERE / "input_calendar_audit.json"
    assert not output.exists(), "Preserve previous input audits"
    plan = json.loads((HERE / "plan_attempt2.json").read_text())
    record = plan["data_receipts"]
    assert sha(Path(record["path"])) == record["sha256"]
    receipts = json.loads(Path(record["path"]).read_text())
    native_begin = pd.Timestamp(plan["start"]) - pd.Timedelta(minutes=128 * 15)
    end = pd.Timestamp(plan["end"])
    navgrid = pd.date_range(plan["start"], plan["comparison_end"], freq="h").as_unit("ns")
    results = []
    for asset in ["BTC", "ETH"]:
        tables = {}
        for timeframe, freq in [("5m", "5min"), ("15m", "15min"), ("1h", "h")]:
            path = Path(plan["datadir"]) / (asset + "_USDT-" + timeframe + ".feather")
            assert sha(path) == receipts[path.name]
            frame = pd.read_feather(path)
            assert not frame.date.duplicated().any() and frame.date.is_monotonic_increasing
            frame = frame.loc[frame.date.ge(native_begin) & frame.date.lt(end)].set_index("date")
            frame.index = frame.index.as_unit("ns")
            expected = pd.date_range(native_begin, end, freq=freq, inclusive="left").as_unit("ns")
            assert frame.index.as_unit("ns").equals(expected)
            assert np.isfinite(frame[["open", "high", "low", "close", "volume"]]).all().all()
            assert frame[["open", "high", "low", "close"]].gt(0).all().all()
            assert frame.volume.ge(0).all()
            assert frame.high.ge(frame[["open", "close"]].max(axis=1)).all()
            assert frame.low.le(frame[["open", "close"]].min(axis=1)).all()
            tables[timeframe] = frame
        bars = tables["5m"]
        aggregate = bars.resample("15min").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        )
        assert bars.close.resample("15min").count().eq(3).all()
        pd.testing.assert_frame_equal(aggregate, tables["15m"], check_freq=False)
        hourly = bars.resample("h").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        )
        assert bars.close.resample("h").count().eq(12).all()
        # Marks are native OPEN values before this hour's fills, not close/high/low.
        np.testing.assert_array_equal(tables["1h"].open, hourly.open)
        marks = tables["1h"].open.reindex(navgrid)
        assert len(marks) == 14592 and marks.notna().all()
        differences = {
            c: float(np.max(np.abs(hourly[c].to_numpy() - tables["1h"][c].to_numpy())))
            for c in ["open", "high", "low", "close", "volume"]
        }
        results.append(
            {
                "asset": asset,
                "native_begin": str(native_begin),
                "rows": {k: len(v) for k, v in tables.items()},
                "complete_hourly_nav_marks": len(marks),
                "main15m_exact_from_three_raw5m_bars": True,
                "hourly_open_marks_exact_from_raw5m_open": True,
                "other_hourly_aggregation_max_abs_differences": differences,
                "all_input_file_sha256_verified": True,
            }
        )
    result = {
        "passed": True,
        "verified_at": datetime.now(UTC).isoformat(),
        "plan_sha256": sha(HERE / "plan_attempt2.json"),
        "assets": results,
        "missing_fill_or_mark_values_imputed": False,
        "candidate_calculator_or_account_engine_imported": False,
        "historical_venue_rule_or_first_seen_certification": False,
        "financial_returns_proven": False,
    }
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": True,
                "assets": len(results),
                "calendar_hours_per_asset": 14592,
                "financial_returns_proven": False,
            }
        )
    )


if __name__ == "__main__":
    main()
