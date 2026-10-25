"""Missing monthly archive rows must be supplemented before rejecting a market."""

from zipfile import ZipFile

import pandas as pd
import pytest

import research.crypto.archive_history as history


def test_daily_archive_supplements_a_partial_month(tmp_path, monkeypatch):
    files = {}
    for period, day, price in [("2024-01", "2024-01-01", 10), ("2024-01-02", "2024-01-02", 20)]:
        path = tmp_path / (period + ".zip")
        epoch = int(pd.Timestamp(day, tz="UTC").timestamp() * 1000)
        with ZipFile(path, "w") as archive:
            archive.writestr(
                "data.csv",
                f"{epoch},{price},{price + 1},{price - 1},{price},1,{epoch + 86399999},100\n",
            )
        files[period] = {"path": str(path)}
    monkeypatch.setattr(history, "archive", lambda symbol, tf, period, cache: files[period])
    frame, sources = history.daily_history("SOLUSDT", "2024-01-01", "2024-01-03", tmp_path)
    assert frame.open.tolist() == [10, 20]
    assert len(sources) == 2
    assert frame.date.tolist() == list(pd.date_range("2024-01-01", periods=2, freq="D", tz="UTC"))


def test_daily_reconstruction_requires_24_actual_hours():
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=24, freq="h", tz="UTC"),
            "open": range(100, 124),
            "high": range(101, 125),
            "low": range(99, 123),
            "close": [100.5 + i for i in range(24)],
            "volume": 1.0,
            "quote_volume": 2.0,
        }
    )
    day = history.aggregate_day(frame, "2024-01-01").iloc[0]
    assert (day.open, day.high, day.low, day.close, day.volume, day.quote_volume) == (
        100,
        124,
        99,
        123.5,
        24,
        48,
    )
    with pytest.raises(ValueError, match="Missing or duplicate"):
        history.aggregate_day(frame.drop(index=4), "2024-01-01")
