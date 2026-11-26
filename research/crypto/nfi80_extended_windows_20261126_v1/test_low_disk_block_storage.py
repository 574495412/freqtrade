from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from research.crypto.nfi80_extended_windows_20261126_v1 import low_disk_block_storage as blocks


def test_native_slices_roundtrip_across_two_blocks(tmp_path):
    frames = pd.DataFrame({
        "date": pd.date_range("2025-01-01", tz="UTC", periods=6000, freq="5min"),
        "price": np.linspace(1, 2, 6000),
        "tag": pd.Series([None, "sample"] * 3000, dtype=object),
    }, index=pd.RangeIndex(100, 6100))
    original_clock = pd.Timestamp("2025-01-01T00:00:00Z")
    seen = []

    def rebuild(pair, clock):
        assert clock == original_clock
        seen.append(pair)
        return frames.copy()

    owner = SimpleNamespace(rebuild=rebuild, provider=SimpleNamespace(backtest_time=original_clock))
    cache = blocks.AnalyzedBlocks(owner, tmp_path / "cache")
    key = ("TEST/USDT:USDT", "5m", "futures")
    cache[key] = (frames, original_clock)
    view, clock = cache[key]
    assert clock == original_clock and not seen and cache.bytes_stored == 0
    for selector in (slice(0, 0), slice(1000, 2200), slice(-100, None), slice(995, 1995)):
        matching = view.iloc[selector]
        pd.testing.assert_frame_equal(matching, frames.iloc[selector], check_exact=True)
    assert seen == [key[0]]
    cache.resident.clear()
    pd.testing.assert_frame_equal(view.iloc[1800:2400], frames.iloc[1800:2400], check_exact=True)
    assert cache.disk_reads == 2 and cache.rebuilt == 1
    cache.resident.clear()
    cache.disk[(key, 0)][0].write_bytes(b"corrupt")
    with pytest.raises(blocks.ReplayStorageError, match="block changed"):
        view.iloc[100:200]
