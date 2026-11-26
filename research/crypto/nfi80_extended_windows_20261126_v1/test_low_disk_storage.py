from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from research.crypto.nfi80_extended_windows_20261126_v1.low_disk_storage import (
    AnalyzedFrames, ReplayStorageError,
)


def test_cold_reads_eviction_and_exact_rebuild(tmp_path):
    clock = pd.Timestamp("2025-01-01T00:00:00Z")
    frame = pd.DataFrame({
        "date": pd.date_range(clock, periods=1000, freq="5min"),
        "price": np.linspace(1, 2, 1000),
        "tag": pd.Series([None, "tag"] * 500, dtype=object),
    })
    keys = [(pair, "5m", "futures") for pair in ("A", "B")]
    source = {"A": frame, "B": frame.assign(price=frame.price * 2)}
    called = []

    def rebuild(pair, at):
        assert at == clock
        called.append(pair)
        return source[pair].copy()

    owner = SimpleNamespace(rebuild=rebuild, provider=SimpleNamespace(backtest_time=clock))
    cache = AnalyzedFrames(owner, tmp_path / "cache", 10_000_000, 1)
    for name in keys:
        cache[name] = (source[name[0]], clock)
    assert cache.bytes_stored == 0 and called == []
    for name in keys:
        restored, at = cache[name]
        assert at == clock
        pd.testing.assert_frame_equal(restored, source[name[0]], check_exact=True)
    assert called == ["A", "B"]
    pd.testing.assert_frame_equal(cache[keys[0]][0], source["A"], check_exact=True)
    assert cache.disk_reads == 1
    cache.disk_limit = max(item[1] for item in cache.stored.values())
    cache.resident.clear()
    cache._forget_oldest()
    assert cache.evicted == 1
    pd.testing.assert_frame_equal(cache[keys[1]][0], source["B"], check_exact=True)
    assert called == ["A", "B", "B"] and cache.evicted == 2
    cache.resident.clear()
    cache.stored[keys[1]][0].write_bytes(b"corrupt")
    with pytest.raises(ReplayStorageError, match="cache changed"):
        cache[keys[1]]


def test_rebuild_must_equal_initial_native_full_frame(tmp_path):
    original = pd.DataFrame({"price": [1.0, 2.0]})
    owner = SimpleNamespace(rebuild=lambda *_: original.assign(price=3.0),
                            provider=SimpleNamespace(backtest_time=None))
    cache = AnalyzedFrames(owner, tmp_path / "cache", 10000, 1)
    key = ("A", "5m", "futures")
    cache[key] = (original, None)
    with pytest.raises(ReplayStorageError, match="reanalysis differed"):
        cache[key]
    assert not cache.resident and cache.bytes_stored == 0
