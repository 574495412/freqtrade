"""Seal the existing40-coin archive for upstream-factor reproduction, without fake rates."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, read_archive, sha
from research.crypto.trend_mainline.data import validate


def prepare(output):  # noqa: C901
    if (output / "completion.json").exists():
        raise ValueError("Immutable dataset already completed")
    base = Path(__file__).resolve().parents[2] / "user_data/crypto_quant"
    raw = base / "nfi40_data_v002_klines"
    native = base / "nfi40_data_v002"
    symbols = read(native / "plan.json")["symbols"]
    rr = read(raw / "receipts.json")
    nr = read(native / "receipts.json")
    plan = {
        "symbols": symbols,
        "bar_start": "2023-08-01",
        "funding_start": "2023-12-01",
        "end": "2026-09-01",
        "source_sha256": sha(Path(__file__)),
        "parent_receipts": {
            str(raw / "receipts.json"): sha(raw / "receipts.json"),
            str(native / "receipts.json"): sha(native / "receipts.json"),
        },
        "calendar": "actual historical1h/4h/8h funding events; never assume three events per day",
        "schedule_exception": (
            "2026-06-24 04h absent for4contracts in both sealed data "
            "and current official funding API; "
            "do not synthesize zero or a settlement"
        ),
    }
    with (output / "prepare_plan.json").open("x") as stream:
        json.dump(plan, stream, indent=2)
    snapshot = output / "source_snapshot"
    snapshot.mkdir(exist_ok=True)
    with (snapshot / Path(__file__).name).open("xb") as stream:
        stream.write(Path(__file__).read_bytes())

    def verify(root, name, receipts):
        p = root / name
        if sha(p) != receipts[name]:
            raise ValueError("Unsealed source: " + name)
        return p

    def one(symbol):
        pieces = []
        sources = {}
        for month in pd.period_range(plan["bar_start"], "2026-08", freq="M"):
            name = f"raw/{symbol}/5m/{symbol}-5m-{month}.zip"
            path = verify(raw, name, rr)
            if path.with_suffix(".zip.CHECKSUM").read_text().split()[0] != sha(path):
                raise ValueError("Official checksum changed")
            pieces.append(read_archive(path))
            sources[str(path)] = sha(path)
        frame = pd.concat(pieces).set_index("date").sort_index()
        validate(frame, plan["bar_start"], plan["end"], "5min")
        pair = symbol.removesuffix("USDT") + "_USDT_USDT"
        path = verify(native, f"candles/futures/{pair}-5m-futures.feather", nr)
        n = pd.read_feather(path).set_index("date")
        np.testing.assert_allclose(frame[n.columns].to_numpy(), n.to_numpy(), rtol=1e-12, atol=1e-8)
        sources[str(path)] = sha(path)
        path = verify(native, symbol + "_funding_events.parquet", nr)
        events = pd.read_parquet(path).set_index("date")
        sources[str(path)] = sha(path)
        if events.index.has_duplicates or not events.index.is_monotonic_increasing:
            raise ValueError("Invalid settlement identity")
        gap = events.index.to_series().diff().dt.total_seconds() / 3600
        maximum = pd.concat(
            [events.funding_interval_hours, events.funding_interval_hours.shift(1)], axis=1
        ).max(axis=1)
        exceptions = events.index[gap > maximum]
        connector = None
        if len(exceptions):
            connector = output / (symbol + "_repair_connector.json")
            official = read(connector)["result"]
            actual = pd.to_datetime(
                [r["fundingTime"] for r in official], unit="ms", utc=True
            ).floor("h")
            if (
                list(exceptions) != [pd.Timestamp("2026-06-24T08:00Z")]
                or pd.Timestamp("2026-06-24T04:00Z") in actual
            ):
                raise ValueError("Unresolved funding gap")
            for t, e in zip(actual, official, strict=True):
                if (
                    t not in events.index
                    or abs(events.loc[t, "funding_rate"] - float(e["fundingRate"])) > 1e-12
                ):
                    raise ValueError("Official gap reconciliation failed")
            sources[str(connector)] = sha(connector)
        path = verify(native, f"candles/futures/{pair}-1h-mark.feather", nr)
        marks = pd.read_feather(path).set_index("date").open.reindex(events.index)
        sources[str(path)] = sha(path)
        if marks.isna().any() or not np.isfinite(events.funding_rate).all() or (marks <= 0).any():
            raise ValueError("Missing actual rate or mark proxy")
        if events.index[0] != pd.Timestamp(plan["funding_start"], tz="UTC") or pd.Timestamp(
            plan["end"], tz="UTC"
        ) - events.index[-1] > pd.Timedelta(hours=int(events.funding_interval_hours.iloc[-1])):
            raise ValueError("Funding boundaries incomplete")
        funding = pd.DataFrame({"funding_rate": events.funding_rate, "mark": marks})
        frame.to_parquet(output / (symbol + "_5m.parquet"))
        funding.to_parquet(output / (symbol + "_funding.parquet"))
        return {
            "symbol": symbol,
            "bars": len(frame),
            "funding_events": len(funding),
            "funding_intervals": sorted(events.funding_interval_hours.unique().tolist()),
            "reconciled_schedule_exceptions": [t.isoformat() for t in exceptions],
            "sources": sources,
        }

    with ThreadPoolExecutor(max_workers=4) as pool:
        coverage = list(pool.map(one, symbols))
    with (output / "coverage.json").open("x") as stream:
        json.dump(coverage, stream, indent=2)
    manifest = {p.name: sha(p) for p in output.glob("*.parquet")}
    with (output / "completion.json").open("x") as stream:
        json.dump(
            {
                "manifest": manifest,
                "plan_sha256": sha(output / "prepare_plan.json"),
                "coverage_sha256": sha(output / "coverage.json"),
            },
            stream,
            indent=2,
        )
    print(
        {
            "symbols": len(symbols),
            "bars": sum(r["bars"] for r in coverage),
            "schedule_exceptions": sum(len(r["reconciled_schedule_exceptions"]) for r in coverage),
        },
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    prepare(parser.parse_args().output.resolve())
