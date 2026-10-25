"""Freeze weekly funding ranks from actual events without reading return labels."""

import argparse
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.strategies.funding_rotation import load_factor, select_week


def prepare(plan_path, output):
    plan = read(plan_path)
    dataset = Path(plan["dataset"])
    if sha(dataset / "receipts.json") != plan["data_receipts_sha256"]:
        raise ValueError("Parent dataset identity changed")
    receipts = read(dataset / "receipts.json")
    factor_source = plan["factor_source"]
    factor = load_factor(Path(factor_source["path"]), factor_source["sha256"])
    output.mkdir(exist_ok=False)
    save(output / "protocol.json", plan)
    start = pd.Timestamp("2023-12-01", tz="UTC")
    end = pd.Timestamp(plan["holdout_end"], tz="UTC")
    hours = pd.date_range(start, end, inclusive="left", freq="h")
    snapshots = hours[(hours.dayofweek == 0) & (hours.hour == 1)]
    clocks = snapshots.as_unit("ms").asi8.tolist()
    values = {}
    for symbol in plan["symbols"]:
        path = dataset / f"{symbol}_funding_events.parquet"
        if sha(path) != receipts[path.name]:
            raise ValueError("Parent funding event file changed")
        raw = pd.read_parquet(path)
        events = [
            {"symbol": symbol, "time_ms": int(row.calc_time), "rate": float(row.funding_rate)}
            for row in raw.itertuples()
        ]
        result = factor.calculate(
            clocks, events, int(start.timestamp() * 1000), int(end.timestamp() * 1000) - 1
        )
        values[symbol] = result.funding_carry_24h.to_numpy()
    rows = []
    for index, snapshot in enumerate(snapshots):
        rows.extend(select_week(snapshot, {s: values[s][index] for s in values}, plan["symbols"]))
    panel = pd.DataFrame(rows)
    panel.to_parquet(output / "panel.parquet", index=False)
    result = {
        "snapshots": len(snapshots),
        "eligible_snapshots": int(panel.groupby("date").eligible_week.first().sum()),
        "rows": len(panel),
        "factor_source_sha256": factor_source["sha256"],
        "parent_receipts_sha256": plan["data_receipts_sha256"],
        "source_sha256": sha(Path(__file__)),
        "price_returns_read": False,
        "orders_enabled": False,
    }
    save(output / "result.json", result)
    save(
        output / "receipts.json", {p.name: sha(p) for p in sorted(output.iterdir()) if p.is_file()}
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(prepare(args.plan.resolve(), args.output.resolve()))
