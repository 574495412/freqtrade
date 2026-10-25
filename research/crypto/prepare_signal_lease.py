"""Expand frozen parent predictions into bounded causal lease packets without outcomes."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import save, sha


def expand(parents):
    parents = parents.copy()
    parents["parent_date"] = parents.date
    parents["parent_feature_open"] = parents.date - pd.Timedelta(minutes=5)
    parents["parent_row_id"] = parents.row_id.astype(int)
    parents["lease_id"] = parents.symbol + ":" + parents.row_id.astype(str)
    if parents.empty:
        return parents
    rows = []
    for row in parents.to_dict("records"):
        for date in pd.date_range(
            row["date"] + pd.Timedelta(minutes=5), row["date"] + pd.Timedelta(hours=20), freq="5min"
        ):
            rows.append(dict(row, date=date, forecast_sleeve="small"))
    small = pd.DataFrame(rows).sort_values(["date", "symbol", "parent_date"])
    small = small.drop_duplicates(["date", "symbol"], keep="last")
    # Parent core proposals keep priority at their own publication clock.
    full = pd.concat([small, parents.assign(forecast_sleeve="core")], ignore_index=True)
    full = full.drop_duplicates(["date", "symbol"], keep="last").sort_values(["date", "symbol"])
    full["row_id"] = np.arange(len(full))
    return full.reset_index(drop=True)


def prepare(source_path, source_sha, output):
    if output.exists() or sha(source_path) != source_sha:
        raise ValueError("Existing output or changed parent packet")
    source = pd.read_parquet(source_path)
    parents = source.loc[source.forecast_sleeve.eq("core")].copy()
    if parents.duplicated(["symbol", "date"]).any():
        raise ValueError("Duplicate parent")
    packet = expand(parents)
    prefixes = []
    for cut in ["2024-08-05T12:00:00Z", "2024-09-15T12:00:00Z", "2024-12-15T12:00:00Z"]:
        clock = pd.Timestamp(cut)
        observed = expand(parents.loc[parents.date < clock])
        expected = packet.loc[packet.date < clock]
        pd.testing.assert_frame_equal(
            observed.loc[observed.date < clock].reset_index(drop=True),
            expected.reset_index(drop=True),
            check_exact=True,
        )
        prefixes.append({"cut": cut, "rows_checked": len(expected), "passed": True})
    output.mkdir(parents=True)
    path = output / "ranking.parquet"
    packet.to_parquet(path, index=False)
    hashes = {}
    for symbol in sorted(source.symbol.unique()):
        target = output / f"{symbol}.parquet"
        packet.loc[packet.symbol.eq(symbol)].drop(columns="symbol").to_parquet(target, index=False)
        hashes[symbol] = sha(target)
    result = {
        "prepared_utc": datetime.now(UTC).isoformat(),
        "source_path": str(source_path),
        "source_sha256": source_sha,
        "source_code_sha256": sha(Path(__file__)),
        "parent_forecasts": len(parents),
        "expanded_rows": len(packet),
        "causal_prefixes": prefixes,
        "parent_dates": int(parents.date.dt.floor("D").nunique()),
        "policy": {
            "path": str(output.absolute()),
            "hashes": hashes,
            "ranking_path": str(path.absolute()),
            "ranking_sha256": sha(path),
        },
        "future_outcomes_read": False,
        "production_eligible": False,
        "orders_enabled": False,
    }
    save(output / "receipt.json", result)
    print({k: result[k] for k in ["parent_forecasts", "expanded_rows", "parent_dates"]})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.source, args.source_sha256, args.output)
