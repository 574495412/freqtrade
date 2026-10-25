"""Recover actual taker flow from frozen futures archives and form causal ranks."""

import argparse
import io
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha


def read_flow_archive(path):
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != 1 or not names[0].endswith(".csv"):
            raise ValueError("One official CSV required")
        raw = pd.read_csv(io.BytesIO(archive.read(names[0])), header=None, usecols=[0, 4, 7, 10])
    if raw.iloc[0, 0] in ("open_time", "open_time_ms"):
        raw = raw.iloc[1:]
    raw = raw.apply(pd.to_numeric, errors="raise")
    unit = "us" if raw.iloc[0, 0] > 1e14 else "ms"
    result = pd.DataFrame(
        {
            "date": pd.to_datetime(raw[0], unit=unit, utc=True),
            "close": raw[4].astype(float),
            "quote_volume": raw[7].astype(float),
            "taker_buy_quote": raw[10].astype(float),
        }
    ).reset_index(drop=True)
    numeric = result[["close", "quote_volume", "taker_buy_quote"]]
    if not np.isfinite(numeric.to_numpy()).all() or result.close.le(0).any():
        raise ValueError("Invalid real flow source")
    tolerance = np.maximum(1e-9, result.quote_volume * 1e-12)
    if (
        result.quote_volume.lt(0).any()
        or result.taker_buy_quote.lt(0).any()
        or result.taker_buy_quote.gt(result.quote_volume + tolerance).any()
    ):
        raise ValueError("Taker quote volume exceeds observed total")
    return result


def public_features(close, quotes, taker):
    """Finite trailing windows and current complete population only; no outcomes."""
    if (
        not close.index.equals(quotes.index)
        or not close.index.equals(taker.index)
        or not close.columns.equals(quotes.columns)
        or not close.columns.equals(taker.columns)
    ):
        raise ValueError("Public panel population or clock mismatch")
    returns = 100 * (close / close.shift(4) - 1)
    ratio = quotes / quotes.rolling(6, min_periods=6).mean().shift(1).replace(0, np.nan)
    liquidity = quotes.rolling(12, min_periods=12).sum()
    flow = 2 * taker / quotes.replace(0, np.nan) - 1
    ranks = returns.rank(axis=1, pct=True)
    volume_rank = ratio.rank(axis=1, pct=True)
    liquidity_rank = liquidity.rank(axis=1, pct=True)
    residual = returns.sub(returns.median(axis=1), axis=0)
    valid = np.logical_and.reduce(
        [np.isfinite(x.to_numpy()) for x in (returns, ratio, liquidity, flow)]
    )
    complete = valid.all(axis=1)
    common = complete[:, None] & volume_rank.ge(0.9).to_numpy() & liquidity_rank.ge(0.5).to_numpy()
    long = common & ranks.ge(0.9).to_numpy() & residual.gt(0).to_numpy() & flow.gt(0).to_numpy()
    short = common & ranks.le(0.1).to_numpy() & residual.lt(0).to_numpy() & flow.lt(0).to_numpy()
    direction = long.astype(np.int8) - short.astype(np.int8)
    prior = np.vstack([np.zeros((1, direction.shape[1]), dtype=np.int8), direction[:-1]])
    previous_complete = np.r_[False, complete[:-1]]
    fresh = (direction != 0) & (direction != prior) & previous_complete[:, None]
    volume = np.where(fresh, volume_rank.to_numpy(), -np.inf)
    absolute_residual = np.where(fresh, residual.abs().to_numpy(), -np.inf)
    symbols = np.broadcast_to(np.arange(direction.shape[1]), direction.shape)
    winner = np.lexsort((symbols, -absolute_residual, -volume), axis=1)[:, 0]
    entry = np.zeros_like(direction)
    ready = np.flatnonzero(fresh.any(axis=1))
    entry[ready, winner[ready]] = direction[ready, winner[ready]]
    return {
        "direction": pd.DataFrame(direction, index=close.index, columns=close.columns),
        "entry": pd.DataFrame(entry, index=close.index, columns=close.columns),
        "residual_20_pct": residual,
        "flow_5m": flow,
        "volume_rank": volume_rank,
        "liquidity_rank": liquidity_rank,
    }


def prepare(protocol_path, output):
    plan = read(protocol_path)
    output.mkdir(parents=True, exist_ok=False)
    save(output / "protocol.json", plan)
    stage = Path(plan["klines_stage"])
    if sha(stage / "receipts.json") != plan["klines_receipts_sha256"]:
        raise ValueError("Futures source receipts changed")
    receipts = read(stage / "receipts.json")
    sources = read(stage / "archive_sources.json")
    start, end = (
        pd.Timestamp(plan["feature_start"], tz="UTC"),
        pd.Timestamp(plan["feature_end"], tz="UTC"),
    )
    clock = pd.date_range(start, end, freq="5min", inclusive="left")
    close, quotes, taker, consumed = {}, {}, {}, []
    for symbol in sorted(plan["symbols"]):
        selected = [
            r
            for r in sources
            if r["symbol"] == symbol
            and r["timeframe"] == "5m"
            and r["period"][:7] >= start.strftime("%Y-%m")
            and r["period"][:7] <= (end - pd.Timedelta(days=1)).strftime("%Y-%m")
        ]
        frames = []
        for record in selected:
            path = Path(record["path"])
            if (
                sha(path) != record["sha256"]
                or receipts[str(path.relative_to(stage))] != record["sha256"]
            ):
                raise ValueError("Futures flow archive changed")
            frames.append(read_flow_archive(path))
            consumed.append(record)
        frame = pd.concat(frames).sort_values("date")
        frame = frame.loc[frame.date.ge(start) & frame.date.lt(end)].set_index("date")
        if not frame.index.equals(clock):
            raise ValueError(f"Incomplete real5m flow calendar: {symbol}")
        close[symbol], quotes[symbol], taker[symbol] = (
            frame.close,
            frame.quote_volume,
            frame.taker_buy_quote,
        )
        print({"symbol": symbol, "rows": len(frame)}, flush=True)
    features = public_features(pd.DataFrame(close), pd.DataFrame(quotes), pd.DataFrame(taker))
    counts = {"long": 0, "short": 0}
    for symbol in sorted(plan["symbols"]):
        frame = (
            pd.DataFrame({name: value[symbol] for name, value in features.items()})
            .rename_axis("date")
            .reset_index()
        )
        frame.to_parquet(output / f"{symbol}.parquet", index=False)
        counts["long"] += int(frame.entry.eq(1).sum())
        counts["short"] += int(frame.entry.eq(-1).sum())
    save(output / "sources.json", consumed)
    save(
        output / "result.json",
        {
            "protocol_sha256": sha(protocol_path),
            "preparer_sha256": sha(Path(__file__)),
            "raw_archives": len(consumed),
            "symbols": len(plan["symbols"]),
            "calendar_rows_per_symbol": len(clock),
            "fresh_market_events": counts,
            "price_returns_scored": False,
            "orders_enabled": False,
        },
    )
    save(
        output / "receipts.json",
        {str(p.relative_to(output)): sha(p) for p in sorted(output.rglob("*")) if p.is_file()},
    )
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(prepare(args.protocol.absolute(), args.output.absolute()), flush=True)
