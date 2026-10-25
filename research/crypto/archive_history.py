"""Read complete real calendars across official monthly and daily archives."""

from pathlib import Path

import pandas as pd

from freqtrade.research.data import read_archive
from research.crypto.prepare_nfi import archive, complete


def aggregate_day(hours: pd.DataFrame, day: str) -> pd.DataFrame:
    end = (pd.Timestamp(day) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    hours = complete(hours, day, end, "h")
    return (
        hours.set_index("date")
        .resample("D")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
                "quote_volume": "sum",
            }
        )
        .reset_index()
    )


def daily_history(symbol: str, start: str, end: str, cache: Path):
    sources, frames = [], []
    for period in pd.period_range(start, pd.Timestamp(end) - pd.Timedelta(days=1), freq="M"):
        record = archive(symbol, "1d", str(period), cache)
        sources.append(record)
        frames.append(read_archive(Path(record["path"])))
    frame = pd.concat(frames)
    expected = pd.date_range(start, end, inclusive="left", freq="D", tz="UTC")
    missing = expected.difference(pd.DatetimeIndex(frame.date))
    for timestamp in missing:
        day = timestamp.strftime("%Y-%m-%d")
        try:
            record = archive(symbol, "1d", day, cache)
            supplement = read_archive(Path(record["path"]))
        except RuntimeError as error:
            if "404" not in str(error):
                raise
            record = archive(symbol, "1h", day[:7], cache)
            supplement = aggregate_day(read_archive(Path(record["path"])), day)
            record = dict(
                record,
                derived_timeframe="1d",
                covered_day=day,
                aggregation="24 actual hourly candles; UTC OHLC and volume sums",
            )
        sources.append(record)
        frame = pd.concat([frame, supplement.loc[supplement.date.eq(timestamp)]])
    return complete(frame, start, end, "D"), sources
