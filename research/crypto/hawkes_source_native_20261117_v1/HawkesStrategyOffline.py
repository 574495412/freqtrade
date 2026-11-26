"""Offline prehistory through IStrategy, without changing exchange capabilities.

The native account starts on its registered date. The full fixed historical
origin is used only to compute the unchanged source indicators, and rows at or
after the native frame's first candle are never prepended from disk.
"""

import hashlib
import io
from pathlib import Path

import numpy as np
import pandas as pd
from HawkesInputs import validate_input
from HawkesStrategy import HawkesStrategy


class HawkesStrategyOffline(HawkesStrategy):
    startup_candle_count = 0
    _origin = pd.Timestamp("2023-08-01T00:00:00Z")
    _native_start = pd.Timestamp("2024-12-31T23:00:00Z")
    _price_columns = ["open", "high", "low", "close", "volume"]
    _allowed_pairs = {"BTC/USDT:USDT", "ETH/USDT:USDT"}

    def _read_history(self, pair):
        mode = self.config.get("runmode")
        mode = getattr(mode, "value", mode)
        if (
            not self.config.get("research_hawkes")
            or self.config.get("dry_run") is not True
            or self.config.get("trading_mode") != "futures"
            or mode != "backtest"
            or pair not in self._allowed_pairs
        ):
            raise ValueError("Offline history is restricted to the fixed historical research")
        record = self.config.get("research_hawkes_history", {}).get(pair)
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            raise ValueError("A pinned path/SHA256 history record is required per pair")
        path = Path(record["path"])
        if not path.is_absolute():
            raise ValueError("History source path must be absolute")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise ValueError("Frozen offline candle history hash differs")
        # Read the verified bytes, not a second mutable file-system read.
        history = pd.read_feather(io.BytesIO(raw))
        validate_input(history)
        if history.date.iloc[0] != self._origin:
            raise ValueError("The frozen source origin must remain 2023-08-01T00:00:00Z")
        return history

    def _assemble_history(self, dataframe, pair):
        history = self._read_history(pair)
        required = ["date", *self._price_columns]
        if not set(required).issubset(history) or not set(required).issubset(dataframe):
            raise ValueError("Both native and frozen inputs must contain the complete OHLCV")
        if dataframe.empty:
            raise ValueError("Native input must not be empty")
        dates = dataframe.date
        if (
            not isinstance(dates.dtype, pd.DatetimeTZDtype)
            or str(dates.dt.tz) != "UTC"
            or dates.isna().any()
            or not dates.eq(dates.dt.floor("h")).all()
            or not dates.diff().iloc[1:].eq(pd.Timedelta(hours=1)).all()
        ):
            raise ValueError("Native dates must be uninterrupted unique UTC hour opens")
        if dates.iloc[0] < self._native_start:
            raise ValueError("Native execution data starts before its registered boundary")
        indexed = history.set_index("date")
        if not dates.isin(indexed.index).all():
            raise ValueError("Native timestamps are outside the frozen source history")
        frozen_values = indexed.loc[dates, self._price_columns].to_numpy(dtype=float)
        native_values = dataframe[self._price_columns].to_numpy(dtype=float)
        if not np.isfinite(frozen_values).all() or not np.array_equal(native_values, frozen_values):
            raise ValueError("Native OHLCV differs from the frozen source history")
        if not history.volume.ge(0).all():
            raise ValueError("Frozen candle volumes must be nonnegative")
        before = history.loc[history.date < dates.iloc[0], required]
        combined = pd.concat([before, dataframe[required]], ignore_index=True)
        validate_input(combined)
        if combined.date.iloc[-1] != dates.iloc[-1]:
            raise ValueError("Offline indicator assembly included a future candle")
        return combined, len(before)

    def populate_indicators(self, dataframe, metadata):
        combined, prehistory_rows = self._assemble_history(dataframe, metadata.get("pair"))
        calculated = super().populate_indicators(combined, metadata)
        tail = calculated.iloc[prehistory_rows:]
        if len(tail) != len(dataframe):
            raise ValueError("Offline indicator tail has the wrong number of native rows")
        for column in ["hawkes_atr", "hawkes_norm_range", "hawkes_v_hawk", "hawkes_signal"]:
            # Position-based assignment preserves a nonzero/custom native index.
            dataframe[column] = tail[column].to_numpy(copy=True)
        return dataframe
