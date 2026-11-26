"""Supported get_pair_precision route matching the old NFI5m heuristic exactly.

This intentionally retains the old whole-month information gap. It neither
certifies historical exchange rules nor repairs the heuristic's lookahead.
"""

import hashlib
import io
from math import isnan
from pathlib import Path

import pandas as pd
from ccxt import TICK_SIZE


class LegacyFiveMinutePrecisionMixin:
    def setup_legacy_five_minute_precision(self, plan):
        mode = getattr(self.config.get("runmode"), "value", self.config.get("runmode"))
        if (
            mode != "backtest"
            or self.config.get("dry_run") is not True
            or not self.config.get("research_head_shoulders")
            or self.config["exchange"]["pair_whitelist"] != plan["pairs"]
        ):
            raise ValueError("Shared legacy precision is restricted to the frozen research")
        record = plan["legacy5m_reference"]
        raw = Path(record["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise ValueError("Frozen legacy5m precision reference changed")
        table = pd.read_parquet(io.BytesIO(raw))
        if table.pair.drop_duplicates().tolist() != plan["pairs"]:
            raise ValueError("Legacy reference pair order differs")
        self._legacy5m_precision = {}
        for pair in plan["pairs"]:
            rows = table.loc[table.pair == pair]
            if rows.month.duplicated().any() or not rows.month.is_monotonic_increasing:
                raise ValueError("Legacy reference monthly calendar differs")
            self._legacy5m_precision[pair] = pd.Series(
                rows.nfi_tick.to_numpy(copy=True), index=pd.DatetimeIndex(rows.month)
            )
        self._legacy5m_start = pd.Timestamp(plan["start"])
        self._legacy5m_end = pd.Timestamp(plan["end"])

    def get_pair_precision(self, pair, current_time):
        if not hasattr(self, "_legacy5m_precision") or pair not in self._legacy5m_precision:
            raise ValueError("Shared legacy precision was not initialized for pair")
        now = pd.Timestamp(current_time)
        if now.tzinfo is None or not self._legacy5m_start <= now < self._legacy5m_end:
            raise ValueError("Precision request outside registered financial clock")
        # Exact original native .asof semantics, including skipping NaN values.
        precision = self._legacy5m_precision[pair].asof(current_time)
        if not isnan(precision):
            return precision, TICK_SIZE
        return self.exchange.get_precision_price(pair), self.precision_mode_price
