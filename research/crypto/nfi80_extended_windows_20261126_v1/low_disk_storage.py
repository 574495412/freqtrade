"""Bound native replay storage by regenerating evicted frames exactly."""

from collections import OrderedDict
from collections.abc import Mapping
import hashlib
import io
import pickle

import pandas as pd
import pyarrow as pa

from research.crypto.nfi40_frame_cache_native_20261117_v1.frame_storage_v3 import semantic_digest


class ReplayStorageError(BaseException):
    """An integrity failure must escape the native strategy callback fallback."""


class IndicatorFrames(Mapping):
    def __init__(self, raw, operation, provider):
        self.raw = raw
        self.operation = operation
        self.provider = provider
        self.clock = provider.backtest_time
        self.pending = dict.fromkeys(raw)
        self.calls = 0

    def build(self, pair):
        current = self.provider.backtest_time
        base = self.raw[pair]
        try:
            self.provider._set_dataframe_max_date(self.clock)
            computed = self.operation({pair: base})[pair]
        finally:
            self.provider._set_dataframe_max_date(current)
        pd.testing.assert_index_equal(computed.index, base.index, exact=True)
        pd.testing.assert_series_equal(computed["date"], base["date"], check_exact=True)
        self.calls += 1
        return computed

    def __getitem__(self, pair):
        if pair not in self.pending:
            raise KeyError(pair)
        return self.build(pair)

    def __iter__(self):
        return iter(self.pending)

    def __len__(self):
        return len(self.pending)

    def consume(self, pair):
        del self.pending[pair]
        return 0


class AnalyzedFrames:
    """Remember every analyzed fingerprint, retaining recently accessed payloads."""

    def __init__(self, owner, root, disk_limit, memory_frames):
        self.owner = owner
        self.root = root
        root.mkdir()
        self.disk_limit = disk_limit
        self.memory_frames = memory_frames
        self.records = {}
        self.resident = OrderedDict()
        self.stored = OrderedDict()
        self.bytes_stored = 0
        self.peak_bytes = 0
        self.rebuilt = 0
        self.disk_reads = 0
        self.evicted = 0

    def __contains__(self, key):
        return key in self.records

    def __setitem__(self, key, value):
        if key in self.records:
            raise ReplayStorageError("Unexpected repeated native analyzed-frame assignment")
        frame, refreshed = value
        self.records[key] = (semantic_digest(frame), refreshed, self.owner.provider.backtest_time)

    def _forget_oldest(self):
        _, (path, size, _) = self.stored.popitem(last=False)
        path.unlink()
        self.bytes_stored -= size
        self.evicted += 1

    def _store(self, key, frame):
        token = hashlib.sha256(pickle.dumps(key, protocol=5)).hexdigest()
        path = self.root / f"{token}.pkl"
        temporary = path.with_suffix(".writing")
        with pa.CompressedOutputStream(str(temporary), "zstd") as stream:
            pickle.dump(frame, stream, protocol=5)
        raw = temporary.read_bytes()
        size = len(raw)
        if size > self.disk_limit:
            temporary.unlink()
            return
        while self.bytes_stored + size > self.disk_limit:
            self._forget_oldest()
        temporary.replace(path)
        self.stored[key] = (path, size, hashlib.sha256(raw).digest())
        self.bytes_stored += size
        self.peak_bytes = max(self.peak_bytes, self.bytes_stored)

    def _load(self, key):
        path, size, digest = self.stored[key]
        raw = path.read_bytes()
        if len(raw) != size or hashlib.sha256(raw).digest() != digest:
            raise ReplayStorageError(f"Generated frame cache changed for {key[0]}")
        with pa.CompressedInputStream(io.BytesIO(raw), "zstd") as stream:
            with io.BufferedReader(stream) as buffered:
                frame = pickle.load(buffered)  # Only this process's checksum-verified cache.
        self.stored.move_to_end(key)
        self.disk_reads += 1
        return frame

    def __getitem__(self, key):
        expected, refreshed, signal_clock = self.records[key]
        try:
            if key in self.resident:
                self.resident.move_to_end(key)
                return self.resident[key], refreshed
            if key in self.stored:
                frame = self._load(key)
            else:
                frame = self.owner.rebuild(key[0], signal_clock)
                if semantic_digest(frame) != expected:
                    raise ReplayStorageError(f"Native reanalysis differed from the original for {key[0]}")
                self.rebuilt += 1
                self._store(key, frame)
            self.resident[key] = frame
            while len(self.resident) > self.memory_frames:
                self.resident.popitem(last=False)
            return frame, refreshed
        except Exception as error:
            raise ReplayStorageError(f"Native analyzed frame unavailable for {key[0]}") from error


class ReplayStorage:
    def __init__(self, account):
        self.account = account
        self.provider = account.dataprovider
        self.caches = []
        self.indicators = None

    def advise(self, raw, operation):
        self.indicators = IndicatorFrames(raw, operation, self.provider)
        return self.indicators

    def rebuild(self, pair, signal_clock):
        prior = self.provider.backtest_time
        try:
            frame = self.indicators.build(pair)
            self.provider._set_dataframe_max_date(signal_clock)
            return self.account.strategy.ft_advise_signals(frame, {"pair": pair})
        finally:
            self.provider._set_dataframe_max_date(prior)

    def install(self, root):
        result = AnalyzedFrames(self, root, 2 * 1024**3, 6)
        self.caches.append(result)
        return result

    def audit(self):
        return {
            "source_indicator_regenerations": self.indicators.calls,
            "provider_assignments": sum(len(cache.records) for cache in self.caches),
            "exact_rebuilds": sum(cache.rebuilt for cache in self.caches),
            "verified_disk_reads": sum(cache.disk_reads for cache in self.caches),
            "evictions": sum(cache.evicted for cache in self.caches),
            "peak_analyzed_cache_bytes": sum(cache.peak_bytes for cache in self.caches),
            "orders_enabled": False,
        }


def trim_dates(frames, native_trim, timerange, startup_candles):
    if not isinstance(frames, IndicatorFrames):
        raise ReplayStorageError("Unexpected native input for date preview")
    # Native Backtesting uses this preview solely for the min/max trading clocks.
    # Actual indicators prove their full indices and dates before row conversion.
    dates = {pair: frame[["date"]] for pair, frame in frames.raw.items()}
    return native_trim(dates, timerange, startup_candles)
