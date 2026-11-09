# ruff: noqa: S101
"""Validate lazy native trimming and narrow generated-cache consumption."""

from pathlib import Path

import pandas as pd
import pytest

from freqtrade.configuration import TimeRange
from freqtrade.data.converter import trim_dataframes
from research.crypto.nfi80_capacity_20261109.zstd_storage import DiskMapping
from research.crypto.nfi80_historical_universe_20261109.v003.row_storage import (
    LazyTrimMapping,
    release_consumed,
)


def test_lazy_native_trim_and_cache_consumption(tmp_path):
    frames = {
        "full": pd.DataFrame(
            {
                "date": pd.date_range("2026-01-01", periods=12, freq="5min", tz="UTC"),
                "close": range(12),
            }
        ),
        "empty": pd.DataFrame({"date": pd.to_datetime([], utc=True), "close": []}),
    }
    timerange = TimeRange.parse_timerange("1767225600-1767228600")
    original = trim_dataframes(frames, timerange, 2)
    store = DiskMapping(tmp_path / "cache", 1)
    store.update(frames)
    lazy = LazyTrimMapping(store, lambda value: trim_dataframes(value, timerange, 2))
    assert list(lazy) == list(original)
    for pair in original:
        pd.testing.assert_frame_equal(lazy[pair], original[pair], check_exact=True)
    path = store.paths["full"]
    assert release_consumed(store, "full", tmp_path) > 0
    assert "full" not in store and not path.exists()
    outside = Path("/etc/hosts")
    store.paths["forbidden"] = outside
    with pytest.raises(ValueError, match="non-cache"):
        release_consumed(store, "forbidden", tmp_path)
