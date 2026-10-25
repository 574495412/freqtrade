"""Bind real price/flow/OI/funding sources before fitting fixed factor combinations."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.conditional_factors import barrier_labels, expanded_groups, features
from research.crypto.prepare_perpetual_flow import read_flow_archive
from research.crypto.strategies.funding_rotation import load_factor


def prepare(plan_path, metrics_path, output):  # noqa: C901
    plan = read(plan_path)
    if output.exists():
        raise ValueError("Preserve prior factor panel")
    output.mkdir(parents=True)
    save(output / "plan.json", plan)
    for name, expected in read(metrics_path / "receipts.json").items():
        if sha(metrics_path / name) != expected:
            raise ValueError("Prepared metrics input changed")
    base, stage = Path(plan["base_dataset"]), Path(plan["flow_archives"])
    if sha(base / "receipts.json") != plan["base_receipts_sha256"]:
        raise ValueError("Price/funding source receipt changed")
    if sha(stage / "receipts.json") != plan["flow_archive_receipts_sha256"]:
        raise ValueError("Taker-flow archive receipt changed")
    parent, archive_receipts = read(base / "receipts.json"), read(stage / "receipts.json")
    source_records = read(stage / "archive_sources.json")
    source_dir = output / "sources"
    source_dir.mkdir()
    for filename in [
        "conditional_factors.py",
        "conditional_learning.py",
        "prepare_conditional_panel.py",
    ]:
        path = Path(__file__).with_name(filename)
        (source_dir / filename).write_bytes(path.read_bytes())
    funding_source = read(Path("research/crypto/funding_rotation_plan.json"))["factor_source"]
    funding_path = Path(funding_source["path"])
    if sha(funding_path) != funding_source["sha256"]:
        raise ValueError("TradingAgents funding source changed")
    snapshot = source_dir / "funding.py"
    snapshot.write_bytes(funding_path.read_bytes())
    factor = load_factor(snapshot, funding_source["sha256"])
    save(
        output / "input_freeze.json",
        {
            "plan_sha256": sha(plan_path),
            "metrics_receipts_sha256": sha(metrics_path / "receipts.json"),
            "source_sha256": {p.name: sha(p) for p in source_dir.iterdir() if p.is_file()},
            "funding_source": funding_source,
            "orders_enabled": False,
        },
    )
    start, end = (
        pd.Timestamp(plan["feature_start"], tz="UTC"),
        pd.Timestamp(plan["feature_end"], tz="UTC"),
    )
    calendar = pd.date_range(start, end, inclusive="left", freq="5min").as_unit("ns")
    frames, consumed = {}, []
    for symbol in plan["symbols"]:
        filename = f"candles/futures/{symbol.removesuffix('USDT')}_USDT_USDT-5m-futures.feather"
        if sha(base / filename) != parent[filename]:
            raise ValueError("Native five-minute candles changed")
        native = pd.read_feather(base / filename)
        native["date"] = native.date.dt.as_unit("ns")
        native = native.loc[native.date.ge(start) & native.date.lt(end)].reset_index(drop=True)
        chosen = [
            r
            for r in source_records
            if r["symbol"] == symbol
            and r["timeframe"] == "5m"
            and plan["feature_start"][:7] <= r["period"][:7] < plan["feature_end"][:7]
        ]
        flows = []
        for record in chosen:
            path = Path(record["path"])
            if (
                sha(path) != record["sha256"]
                or archive_receipts[str(path.relative_to(stage))] != record["sha256"]
            ):
                raise ValueError("Actual taker-flow archive changed")
            flows.append(read_flow_archive(path))
            consumed.append(record)
        flow = pd.concat(flows).sort_values("date").reset_index(drop=True)
        flow["date"] = flow.date.dt.as_unit("ns")
        if not pd.DatetimeIndex(native.date).equals(calendar) or not pd.DatetimeIndex(
            flow.date
        ).equals(calendar):
            raise ValueError("Source calendar mismatch")
        if not np.allclose(native.close, flow.close, rtol=1e-12, atol=1e-12):
            raise ValueError("Native close and original archive close differ")
        frames[symbol] = native.merge(
            flow[["date", "quote_volume", "taker_buy_quote"]], on="date", validate="one_to_one"
        )
    btc = frames["BTCUSDT"].set_index("date").close
    label_dir = output / "labels"
    label_dir.mkdir()
    coverage = []
    for symbol in plan["symbols"]:
        frame = frames[symbol]
        event_path = base / f"{symbol}_funding_events.parquet"
        if sha(event_path) != parent[event_path.name]:
            raise ValueError("Actual funding event source changed")
        raw = pd.read_parquet(event_path)
        raw = raw.loc[
            raw.calc_time.ge(start.value // 1_000_000) & raw.calc_time.lt(end.value // 1_000_000)
        ]
        events = [
            {"symbol": symbol, "time_ms": int(row.calc_time), "rate": float(row.funding_rate)}
            for row in raw.itertuples()
        ]
        panel = features(
            frame,
            pd.read_parquet(metrics_path / f"{symbol}.parquet"),
            events,
            btc,
            factor.calculate,
            int(start.value // 1_000_000),
            int(end.value // 1_000_000) - 1,
        )
        panel["symbol"] = symbol
        panel.to_parquet(output / f"{symbol}.parquet", index=False)
        label_frame = frame.loc[
            frame.date.lt(pd.Timestamp(plan["selection_end"], tz="UTC"))
        ].reset_index(drop=True)
        mark_filename = f"candles/futures/{symbol.removesuffix('USDT')}_USDT_USDT-1h-mark.feather"
        if sha(base / mark_filename) != parent[mark_filename]:
            raise ValueError("Funding mark source changed")
        mark = pd.read_feather(base / mark_filename).set_index("date").open
        event_clock = pd.to_datetime(raw.calc_time, unit="ms", utc=True).dt.floor("h")
        funding_marks = (
            mark.reindex(pd.DatetimeIndex(event_clock)).to_numpy() * raw.funding_rate.to_numpy()
        )
        for sleeve in ["small", "core"]:
            spec = plan["labels"][sleeve]
            for side, sign in [("long", 1), ("short", -1)]:
                label = barrier_labels(
                    label_frame,
                    sign,
                    spec["take_price_fraction"],
                    spec["stop_price_fraction"],
                    spec["maximum_bars"],
                    0.0008,
                    raw.calc_time.to_numpy(),
                    funding_marks,
                )
                label.insert(0, "date", label_frame.date)
                label.to_parquet(label_dir / f"{symbol}_{sleeve}_{side}.parquet", index=False)
        coverage.append(
            {
                "symbol": symbol,
                "feature_rows": len(panel),
                "finite_by_group": {
                    group: int(np.isfinite(panel[columns]).all(axis=1).sum())
                    for group, columns in expanded_groups(plan).items()
                },
            }
        )
        print(coverage[-1], flush=True)
    save(output / "flow_sources.json", consumed)
    save(
        output / "result.json",
        {
            "coverage": coverage,
            "reserved_test_labels_computed": False,
            "regression_fitted": False,
            "orders_enabled": False,
        },
    )
    save(
        output / "receipts.json",
        {str(p.relative_to(output)): sha(p) for p in sorted(output.rglob("*")) if p.is_file()},
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.plan.absolute(), args.metrics.absolute(), args.output.absolute())
