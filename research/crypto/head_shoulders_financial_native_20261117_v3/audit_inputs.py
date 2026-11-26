"""Frozen pre-financial 5m to 1h candle-origin checks, one pair at a time."""

import gc
import hashlib
import json
import resource
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).parent.resolve()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    protocol = json.loads((HERE / "input_protocol.json").read_text())
    if sha(__file__) != protocol["audit_code_sha256"]:
        raise ValueError("Frozen input auditor differs")
    records = []
    for pair in protocol["inputs"]:
        if shutil.disk_usage(HERE).free < protocol["minimum_free_disk_bytes"]:
            raise RuntimeError("Insufficient disk for input audit")
        for key in ["one_hour", "five_minute"]:
            if sha(pair[key]["path"]) != pair[key]["sha256"]:
                raise ValueError("Frozen input changed")
        hour = pd.read_feather(pair["one_hour"]["path"])
        detail = pd.read_feather(pair["five_minute"]["path"])
        checks = {}
        for name, frame, interval in [("1h", hour, "1h"), ("5m", detail, "5min")]:
            dates = frame.date
            checks[name + "_continuous_utc"] = bool(
                isinstance(dates.dtype, pd.DatetimeTZDtype)
                and str(dates.dt.tz) == "UTC"
                and not dates.isna().any()
                and dates.eq(dates.dt.floor(interval)).all()
                and dates.diff().iloc[1:].eq(pd.Timedelta(interval)).all()
            )
            values = frame[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
            checks[name + "_finite_valid_prices"] = bool(
                np.isfinite(values).all()
                and (values[:, :4] > 0).all()
                and (values[:, 4] >= 0).all()
            )
        checks["fixed_origin_and_end"] = bool(
            hour.date.iloc[0] == pd.Timestamp(protocol["origin"])
            and hour.date.iloc[-1] == pd.Timestamp(protocol["last_hour_open"])
            and detail.date.iloc[0] == hour.date.iloc[0]
            and detail.date.iloc[-1] == hour.date.iloc[-1] + pd.Timedelta(minutes=55)
        )
        grouped = detail.set_index("date").resample("1h", label="left", closed="left")
        counts = grouped.size()
        aggregate = grouped.agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        )
        checks["twelve_detail_bars_each_hour"] = bool(counts.eq(12).all())
        checks["all_raw_hourly_dates_equal"] = bool(
            aggregate.index.as_unit("ns").equals(pd.DatetimeIndex(hour.date).as_unit("ns"))
        )
        mismatches = {}
        max_volume_error = None
        if checks["all_raw_hourly_dates_equal"]:
            for column in ["open", "high", "low", "close"]:
                mismatch = aggregate[column].to_numpy() != hour[column].to_numpy()
                checks[column + "_exact"] = not bool(mismatch.any())
                mismatches[column] = int(mismatch.sum())
            volume = aggregate.volume.to_numpy()
            original = hour.volume.to_numpy()
            checks["volume_within_frozen_tolerance"] = bool(
                np.isclose(
                    volume, original, rtol=protocol["volume_rtol"], atol=protocol["volume_atol"]
                ).all()
            )
            max_volume_error = float(np.max(np.abs(volume - original)))
        else:
            checks["price_and_volume_comparison_possible"] = False
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss = peak if sys.platform == "darwin" else peak * 1024
        if rss > protocol["maximum_rss_bytes"]:
            raise RuntimeError("Input audit RSS limit exceeded")
        records.append(
            {
                "pair": pair["pair"],
                "passed": all(checks.values()),
                "checks": checks,
                "one_hour_rows": len(hour),
                "five_minute_rows": len(detail),
                "price_mismatch_counts": mismatches,
                "max_absolute_volume_error": max_volume_error,
                "peak_rss_bytes": rss,
                **{key: pair[key] for key in ["one_hour", "five_minute"]},
            }
        )
        print(
            json.dumps(
                {
                    "pair": pair["pair"],
                    "passed": records[-1]["passed"],
                    "price_mismatches": mismatches,
                }
            ),
            flush=True,
        )
        del hour, detail, grouped, aggregate
        gc.collect()
    passed = all(record["passed"] for record in records)
    receipt = {
        "completed_at": datetime.now(UTC).isoformat(),
        "passed": passed,
        "pairs_checked": len(records),
        "ordered_pairs": [r["pair"] for r in records],
        "protocol_sha256": sha(HERE / "input_protocol.json"),
        "records": records,
        "max_absolute_volume_error": max(r["max_absolute_volume_error"] or 0 for r in records),
        "financial_start_called": False,
        "native_financial_accounts": 0,
        "source_hourly_inputs_replaced": False,
        "mismatch_policy": "BLOCK_NO_REWRITE_OR_TOLERANCE_RETUNE",
    }
    with (HERE / "input_validation.json").open("x") as stream:
        json.dump(receipt, stream, indent=2)
        stream.write("\n")
    if not passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
