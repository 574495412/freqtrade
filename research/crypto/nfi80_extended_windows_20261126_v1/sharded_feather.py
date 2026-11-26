"""Read bounded annual data slices through Freqtrade's normal candle pipeline."""

from pathlib import Path

import pandas as pd

from freqtrade.candle_columns import get_candle_dtypes
from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read


class ShardedFeatherDataHandler(FeatherDataHandler):
    """Use immutable Feather shards as the storage backend."""

    def __init__(self, datadir, manifest):
        super().__init__(datadir)
        self.shards = read(checked(manifest))["files"]
        self.checked_parts = set()

    def _verified_path(self, record):
        path = Path(record["path"])
        identity = (path, record["sha256"])
        if identity not in self.checked_parts:
            checked(record)
            self.checked_parts.add(identity)
        return path

    def _ohlcv_load(self, pair, timeframe, timerange, candle_type):
        filename = self._pair_data_filename(self._datadir, pair, timeframe, candle_type=candle_type)
        key = str(filename.relative_to(self._datadir))
        chunks = []
        for block in self.shards.get(key, []):
            path = self._verified_path(block)
            frame = self._load_ohlcv_dataframe(path, timeframe, timerange)
            dates = frame["date"]
            owned = dates.lt(pd.Timestamp(block["stop_exclusive"]))
            if block["start_inclusive"] is not None:
                owned &= dates.ge(pd.Timestamp(block["start_inclusive"]))
            chunks.append(frame.loc[owned])
        if not chunks or all(frame.empty for frame in chunks):
            return self._empty_ohlcv_df(candle_type)
        joined = pd.concat(chunks, ignore_index=True)
        dates = joined["date"]
        if not dates.is_unique or not dates.is_monotonic_increasing:
            raise ValueError(f"Sharded historical candle clocks do not join: {key}")
        result = self._normalize_columns(joined, pair, candle_type).astype(get_candle_dtypes(candle_type))
        result["date"] = result["date"].dt.as_unit("ms")
        return result
