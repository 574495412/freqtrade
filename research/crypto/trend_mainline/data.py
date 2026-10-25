"""Checksummed public 5m prices, quote turnover, hourly mark and funding inputs."""

from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, read_archive, save, sha


def verify(root, relative, receipts):
    path = root / relative
    if relative not in receipts or sha(path) != receipts[relative]:
        raise ValueError(f"Unsealed input: {path}")
    return path


def validate(frame, start, end, freq):
    expected = pd.date_range(start, end, freq=freq, inclusive="left", tz="UTC")
    if not pd.DatetimeIndex(frame.index).equals(expected):
        raise ValueError("Missing, duplicate, out-of-order or extra market clocks")
    if not np.isfinite(frame.to_numpy(dtype=float)).all():
        raise ValueError("Nonfinite market input")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Nonpositive prices")
    if (frame.high < frame[["open", "close", "low"]].max(axis=1)).any() or (
        frame.low > frame[["open", "close", "high"]].min(axis=1)
    ).any():
        raise ValueError("Invalid OHLC")
    if (frame[["volume", "quote_volume"]] < 0).any().any():
        raise ValueError("Negative volume")


def features(frames, plan):
    """All daily fields at time t use days strictly before t; hourly close uses t-1h."""
    daily, result = {}, {}
    for symbol, frame in frames.items():
        d = frame.resample("D").agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
                "quote_volume": "sum",
            }
        )
        prev = d.close.shift(1)
        tr = pd.concat([d.high - d.low, (d.high - prev).abs(), (d.low - prev).abs()], axis=1).max(
            axis=1
        )
        daily[symbol] = pd.DataFrame(
            {
                "atr": tr.rolling(20, min_periods=20).mean().shift(1),
                "high55": d.high.rolling(55, min_periods=55).max().shift(1),
                "low55": d.low.rolling(55, min_periods=55).min().shift(1),
                "high20": d.high.rolling(20, min_periods=20).max().shift(1),
                "low20": d.low.rolling(20, min_periods=20).min().shift(1),
                "momentum": (d.close / d.close.shift(63) - 1).shift(1),
                "daily_turnover": d.quote_volume.rolling(28, min_periods=28).mean().shift(1),
            }
        )
    turnover = pd.DataFrame({s: d.daily_turnover for s, d in daily.items()})
    # Alphabetical tie break is fixed; ranks are computed within the declared six-name pool.
    ranks = turnover[sorted(turnover.columns)].rank(axis=1, ascending=False, method="first")
    for symbol, frame in frames.items():
        h = frame.resample("h").agg({"close": "last", "quote_volume": "sum"})
        clocks = h.index
        d = daily[symbol].reindex(clocks.floor("D"))
        d.index = clocks
        d["previous_close"] = h.close.shift(1)
        d["before_previous_close"] = h.close.shift(2)
        d["prior_hour_quote"] = h.quote_volume.shift(1)
        d["eligible"] = (
            (
                (ranks[symbol] <= plan["liquidity"]["top_n"])
                & (turnover[symbol] >= plan["liquidity"]["minimum_daily_quote_usdt"])
            )
            .reindex(clocks.floor("D"))
            .to_numpy()
        )
        result[symbol] = d
    return result


def prepare(plan, output):
    output.mkdir(parents=True, exist_ok=False)
    market_root, funding_root = Path(plan["market_dataset"]), Path(plan["funding_dataset"])
    km, fm = read(market_root / "receipts.json"), read(funding_root / "receipts.json")
    if (
        sha(market_root / "receipts.json") != plan["market_receipts_sha256"]
        or sha(funding_root / "receipts.json") != plan["funding_receipts_sha256"]
    ):
        raise ValueError("Registered parent receipt changed")
    records, frames, coverage = {}, {}, {}
    for symbol in plan["symbols"]:
        chunks = []
        for month in pd.period_range(
            plan["data_start"], pd.Timestamp(plan["end"]) - pd.Timedelta(days=1), freq="M"
        ):
            relative = f"raw/{symbol}/5m/{symbol}-5m-{month}.zip"
            path = verify(market_root, relative, km)
            checksum = path.with_suffix(".zip.CHECKSUM")
            if checksum.read_text().split()[0] != sha(path):
                raise ValueError("Official checksum mismatch")
            records[str(path)] = sha(path)
            records[str(checksum)] = sha(checksum)
            chunks.append(read_archive(path))
        frame = pd.concat(chunks).set_index("date").sort_index()
        frame = frame.loc[
            (frame.index >= pd.Timestamp(plan["data_start"], tz="UTC"))
            & (frame.index < pd.Timestamp(plan["end"], tz="UTC"))
        ]
        validate(frame, plan["data_start"], plan["end"], "5min")
        pair = symbol.removesuffix("USDT") + "_USDT_USDT"
        native_path = verify(funding_root, f"candles/futures/{pair}-5m-futures.feather", fm)
        native = pd.read_feather(native_path).set_index("date").loc[frame.index]
        if not np.allclose(frame[native.columns], native, rtol=1e-12, atol=1e-8):
            raise ValueError("Raw/native candle mismatch")
        records[str(native_path)] = sha(native_path)
        mark_path = verify(funding_root, f"candles/futures/{pair}-1h-mark.feather", fm)
        funding_path = verify(funding_root, f"{symbol}_funding_events.parquet", fm)
        marks = pd.read_feather(mark_path).set_index("date")
        funding = pd.read_parquet(funding_path)
        if funding.date.duplicated().any() or not funding.date.is_monotonic_increasing:
            raise ValueError("Invalid funding event identity/order")
        # The selected contracts have a complete eight-hour schedule in this sealed dataset.
        expected = pd.date_range(plan["start"], plan["end"], freq="8h", inclusive="left", tz="UTC")
        events = funding.loc[funding.date.isin(expected)].set_index("date")
        if not events.index.equals(expected) or not np.isfinite(events.funding_rate).all():
            raise ValueError("Missing real scheduled funding; zero fill is forbidden")
        mark_values = marks.open.reindex(expected)
        if mark_values.isna().any() or (mark_values <= 0).any():
            raise ValueError("Missing settlement mark")
        events["mark"] = mark_values
        events[["funding_rate", "mark"]].to_parquet(output / f"{symbol}_funding.parquet")
        for path in (mark_path, funding_path):
            records[str(path)] = sha(path)
        frame.to_parquet(output / f"{symbol}_5m.parquet")
        frames[symbol] = frame
        coverage[symbol] = {"five_minute_rows": len(frame), "funding_events": len(events)}
    for symbol, frame in features(frames, plan).items():
        frame.to_parquet(output / f"{symbol}_features.parquet")
    save(
        output / "quality.json",
        {
            "passed": True,
            "coverage": coverage,
            "quote_volume": (
                "actual quote turnover from official archives, not price times base volume"
            ),
            "raw_native_prices_reconciled": True,
            "independent_historical_universe": False,
            "source_files": records,
        },
    )
    save(output / "receipts.json", {p.name: sha(p) for p in sorted(output.iterdir())})
    return coverage


def load(root, plan):
    for name, checksum in read(root / "receipts.json").items():
        if sha(root / name) != checksum:
            raise ValueError("Prepared panel changed")
    return {
        s: {
            "bars": pd.read_parquet(root / f"{s}_5m.parquet"),
            "features": pd.read_parquet(root / f"{s}_features.parquet"),
            "funding": pd.read_parquet(root / f"{s}_funding.parquet"),
        }
        for s in plan["symbols"]
    }
