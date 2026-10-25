"""Reconstruct real activity features directly from original CSV strings with Decimal."""

import argparse
import csv
import io
import math
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from freqtrade.research.data import read, save, sha


def audit(enrollment):  # noqa: C901 - independent sequential source invariants
    receipt = read(enrollment / "receipt.json")
    if sha(enrollment / "all_features.parquet") != receipt["feature_hash"]:
        raise ValueError("Enrolled features changed")
    features = pd.read_parquet(enrollment / "all_features.parquet")
    records = read(enrollment / "sources.json")
    errors, checks = {}, []
    for symbol, group in features.groupby("symbol", sort=True):
        samples = group.iloc[[0, len(group) // 2, -1]]
        months = set()
        for value in samples.feature_open:
            months.update(
                [value.strftime("%Y-%m"), (value - pd.DateOffset(months=1)).strftime("%Y-%m")]
            )
        rows = {}
        for record in records:
            if record["symbol"] != symbol or record["period"] not in months:
                continue
            path = Path(record["path"])
            if sha(path) != record["sha256"]:
                raise ValueError("Original archive changed")
            with ZipFile(path) as archive:
                text = io.TextIOWrapper(archive.open(archive.namelist()[0]), encoding="utf-8")
                for row in csv.reader(text):
                    if row[0] in {"open_time", "open_time_ms"}:
                        continue
                    clock = int(row[0])
                    if clock in rows:
                        raise ValueError("Duplicate original raw clock")
                    rows[clock] = row

        def raw(clock, field, rows=rows):
            return Decimal(rows[clock][field])

        def sums(clock, field, offsets):
            return sum((raw(clock - 300_000 * n, field) for n in offsets), Decimal(0))

        def triggered(clock, side):
            quote = raw(clock, 7)
            if not quote:
                return False
            flow = 2 * raw(clock, 10) / quote - 1
            op, hi, lo, cl = (raw(clock, i) for i in [1, 2, 3, 4])
            return (
                side * flow <= Decimal("-.25")
                and side * (cl - op) >= 0
                and hi > lo
                and side * (2 * cl - hi - lo) / (hi - lo) >= Decimal(".5")
                and quote * 12 >= Decimal("1.5") * sums(clock, 7, range(1, 13))
            )

        for unit in samples.itertuples(index=False):
            clock = unit.feature_open.value // 1_000_000
            sign = Decimal(unit.side)
            quote, count, taker = (raw(clock, i) for i in [7, 8, 10])
            flow = 2 * taker / quote - 1
            flow60 = 2 * sums(clock, 10, range(12)) / sums(clock, 7, range(12)) - 1
            prior_flow60 = 2 * sums(clock, 10, range(1, 13)) / sums(clock, 7, range(1, 13)) - 1
            prior_quote, prior_count = (
                sums(clock, 7, range(1, 13)) / 12,
                sums(clock, 8, range(1, 13)) / 12,
            )
            count_log = (count / prior_count).ln()
            expected = {
                "actual_flow5": sign * flow,
                "actual_flow60": sign * flow60,
                "flow_change": sign * (flow - prior_flow60),
                "flow_price_response": sign
                * (raw(clock, 4) / raw(clock, 1)).ln()
                / Decimal(str(unit.atr_fraction))
                - sign * flow,
                "quote_ratio": (quote / prior_quote).ln(),
                "count_ratio": count_log,
                "trade_size_ratio": ((quote / count) / (prior_quote / prior_count)).ln(),
                "activity_pressure": -sign * flow * count_log,
            }
            if not triggered(clock, sign) or triggered(clock - 300_000, sign):
                raise ValueError("Original Decimal fresh-event condition differs")
            if unit.date != unit.feature_open + pd.Timedelta(minutes=5):
                raise ValueError("Feature publication clock differs")
            for name, value in expected.items():
                observed = getattr(unit, name)
                errors[name] = max(errors.get(name, 0), abs(float(value) - observed))
                if not math.isclose(float(value), observed, rel_tol=1e-9, abs_tol=1e-11):
                    raise ValueError((symbol, clock, name, value, observed))
            checks.append(
                {"symbol": symbol, "row_id": unit.row_id, "features_checked": len(expected)}
            )
    return {
        "checked_utc": datetime.now(UTC).isoformat(),
        "passed": True,
        "enrollment_receipt_sha256": sha(enrollment / "receipt.json"),
        "audit_source_sha256": sha(Path(__file__)),
        "events_checked": len(checks),
        "decimal_feature_checks": sum(x["features_checked"] for x in checks),
        "maximum_absolute_errors": errors,
        "samples": checks,
        "future_outcomes_read": False,
        "orders_enabled": False,
        "production_eligible": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enrollment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Preserve prior source audit")
    result = audit(args.enrollment)
    save(args.output, result)
    print({k: result[k] for k in ["passed", "events_checked", "decimal_feature_checks"]})
