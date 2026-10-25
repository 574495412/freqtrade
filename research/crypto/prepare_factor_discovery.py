"""Recover real aggressor flow from checksummed archives; keep old studies immutable."""

import argparse
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import sha
from research.crypto.prepare_nfi import archive
from research.crypto.trend_mainline.data import validate


SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]
BASE = Path(__file__).resolve().parents[2] / "user_data/crypto_quant"


def read_flow(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != 1 or not names[0].endswith(".csv"):
            raise ValueError("Expected one CSV")
        raw = pd.read_csv(io.BytesIO(z.read(names[0])), header=None)
    if str(raw.iloc[0, 0]).startswith("open_time"):
        raw = raw.iloc[1:]
    raw = raw.apply(pd.to_numeric, errors="raise")
    if raw.shape[1] != 12:
        raise ValueError("Unknown kline schema")
    unit = "us" if raw.iloc[0, 0] > 1e14 else "ms"
    f = pd.DataFrame(
        index=pd.DatetimeIndex(pd.to_datetime(raw[0].to_numpy(), unit=unit, utc=True), name="date")
    )
    for column, name in {
        1: "open",
        2: "high",
        3: "low",
        4: "close",
        5: "volume",
        7: "quote_volume",
        8: "trade_count",
        9: "taker_buy_volume",
        10: "taker_buy_quote_volume",
    }.items():
        f[name] = raw[column].to_numpy(dtype=float)
    if (f.taker_buy_quote_volume < 0).any() or (
        f.taker_buy_quote_volume > f.quote_volume + 1e-6
    ).any():
        raise ValueError("Invalid aggressor quote volume")
    return f


def prepare(output):
    parent = BASE / "trend_mainline_20261025_v001/data"
    cache = BASE / "nfi40_data_v002_klines/raw"
    receipts = json.loads((parent / "receipts.json").read_text())
    if (output / "completion.json").exists():
        raise ValueError("Completed dataset is immutable")
    plan = {
        "symbols": SYMBOLS,
        "start": "2023-08-01",
        "end": "2026-10-01",
        "source_sha256": sha(Path(__file__)),
        "raw_flow": "official taker-buy quote and total quote volume; not price-signed proxy",
        "funding": (
            "old sealed scheduled rates with hourly mark proxy; "
            "September connector actual markPrice; "
            "nominal settlement bucket floor5min, feature available after actual publication+5min"
        ),
    }
    with (output / "prepare_plan.json").open("x") as stream:
        json.dump(plan, stream, indent=2)

    def one(symbol):
        chunks, sources = [], []
        for month in pd.period_range(plan["start"], "2026-09", freq="M"):
            record = archive(symbol, "5m", str(month), cache)
            chunks.append(read_flow(Path(record["path"])))
            sources.append(record)
        frame = pd.concat(chunks)
        validate(frame, plan["start"], plan["end"], "5min")
        old = pd.read_parquet(parent / f"{symbol}_5m.parquet")
        for name in (f"{symbol}_5m.parquet", f"{symbol}_funding.parquet"):
            if sha(parent / name) != receipts[name]:
                raise ValueError("Original sealed inputs changed")
        np.testing.assert_allclose(
            frame.loc[old.index, old.columns].to_numpy(), old.to_numpy(), rtol=1e-12, atol=1e-6
        )
        frame.to_parquet(output / f"{symbol}_5m.parquet")
        raw_path = output / f"{symbol}_funding_connector.json"
        events = pd.DataFrame(json.loads(raw_path.read_text())["result"])
        clocks = pd.to_datetime(events.fundingTime, unit="ms", utc=True)
        september = pd.DataFrame(
            {
                "funding_rate": events.fundingRate.astype(float).to_numpy(),
                "mark": events.markPrice.astype(float).to_numpy(),
            },
            index=pd.DatetimeIndex(clocks.dt.floor("5min"), name="date"),
        )
        expected = pd.date_range("2026-09-01", "2026-10-01", inclusive="left", freq="8h", tz="UTC")
        if not september.index.equals(expected) or not np.isfinite(september.to_numpy()).all():
            raise ValueError("Incomplete or changed September funding calendar")
        rates = pd.concat([pd.read_parquet(parent / f"{symbol}_funding.parquet"), september])
        if rates.index.has_duplicates or not rates.index.is_monotonic_increasing:
            raise ValueError("Funding identity failure")
        rates.to_parquet(output / f"{symbol}_funding.parquet")
        return {
            "symbol": symbol,
            "rows": len(frame),
            "flow_valid_rows": int(frame.quote_volume.gt(0).sum()),
            "sources": sources,
            "connector_sha256": sha(raw_path),
            "funding_events": len(rates),
        }

    with ThreadPoolExecutor(max_workers=6) as pool:
        coverage = list(pool.map(one, SYMBOLS))
    manifest = {p.name: sha(p) for p in output.glob("*.parquet")}
    with (output / "coverage.json").open("x") as stream:
        json.dump(coverage, stream, indent=2)
    with (output / "completion.json").open("x") as stream:
        json.dump(
            {
                "manifest": manifest,
                "coverage_sha256": sha(output / "coverage.json"),
                "plan_sha256": sha(output / "prepare_plan.json"),
            },
            stream,
            indent=2,
        )
    print(
        {
            "symbols": len(coverage),
            "rows": sum(c["rows"] for c in coverage),
            "flow_columns": "actual aggressor",
        },
        flush=True,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    prepare(p.parse_args().output.resolve())
