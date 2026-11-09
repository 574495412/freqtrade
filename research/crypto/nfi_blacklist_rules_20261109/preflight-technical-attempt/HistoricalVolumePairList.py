"""Explicit historical bridge using unchanged upstream native risk filters."""

import hashlib
import json
from pathlib import Path

import pandas as pd

from freqtrade.enums import CandleType, RunMode
from freqtrade.exceptions import OperationalException
from freqtrade.plugins.pairlist.IPairList import IPairList, SupportsBacktesting
from freqtrade.plugins.pairlist.pairlist_helpers import expand_pairlist
from freqtrade.plugins.pairlist.rangestabilityfilter import RangeStabilityFilter
from freqtrade.plugins.pairlist.VolatilityFilter import VolatilityFilter


COLS = ["date", "open", "high", "low", "close", "volume"]


class HistoricalCandleBridge:
    def __init__(self, exchange, frames):
        self.exchange = exchange
        self.frames = frames
        self.day = None
        self.windows = {}

    def __getattr__(self, name):
        return getattr(self.exchange, name)

    def refresh_ohlcv_with_cache(self, pairs, *, lookback_period):
        if self.day is None:
            raise OperationalException("Historical candle clock is missing")
        result = {}
        for key in pairs:
            pair, timeframe, candle_type = key
            if timeframe != "1d" or candle_type != CandleType.FUTURES:
                raise OperationalException("Unsupported historical risk candle contract")
            cache_key = (pair, lookback_period)
            if cache_key not in self.windows:
                frame = self.frames[pair]
                end = frame.close_time.searchsorted(self.day, side="left")
                window = frame.iloc[max(0, end - lookback_period - 1) : end]
                if len(window) != lookback_period + 1 or not (window.close_time < self.day).all():
                    raise OperationalException("Incomplete or noncausal historical risk window")
                self.windows[cache_key] = window[COLS].copy()
            result[key] = self.windows[cache_key]
        return result


class RuleBook:
    def __init__(self, payload, exchange, manager, config):
        self.variant = payload["variant"]
        if self.variant not in ("structural", "native_risk"):
            raise OperationalException("Unknown frozen blacklist policy")
        self.frames = {}
        for pair, record in payload["daily_inputs"].items():
            path = Path(record["path"])
            if (
                path.is_symlink()
                or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]
            ):
                raise OperationalException("Historical risk source checksum differs")
            frame = pd.read_parquet(path)
            if frame.date.duplicated().any() or not frame.date.is_monotonic_increasing:
                raise OperationalException("Historical risk source clock differs")
            self.frames[pair] = frame
        self.blocked = set(expand_pairlist(payload["structural_blacklist"], list(self.frames)))
        self.bridge = HistoricalCandleBridge(exchange, self.frames)
        self.filters = []
        if self.variant == "native_risk":
            for cls, settings in [
                (
                    RangeStabilityFilter,
                    {"lookback_days": 10, "min_rate_of_change": 0.01, "max_rate_of_change": 0.99},
                ),
                (
                    VolatilityFilter,
                    {"lookback_days": 10, "min_volatility": 0.05, "max_volatility": 0.50},
                ),
            ]:
                self.filters.append(cls(self.bridge, manager, config, settings, 0))
        self.cached_day = None
        self.cached_result = None

    def select(self, clock):
        day = pd.Timestamp(clock).normalize()
        if day == self.cached_day:
            return self.cached_result
        self.bridge.day = day
        self.bridge.windows.clear()
        ranks = []
        input_clocks = []
        for pair, frame in self.frames.items():
            if pair in self.blocked:
                continue
            end = frame.close_time.searchsorted(day, side="left")
            if end < 60:
                continue
            last = frame.iloc[end - 1]
            if last.date != day - pd.Timedelta(days=1) or last.quote_volume <= 0:
                continue
            ranks.append((pair, float(last.quote_volume)))
            input_clocks.append(last.close_time)
        ranks.sort(key=lambda x: (-x[1], x[0]))
        pairs = [p for p, _ in ranks]
        reasons = {}
        metrics = {}
        for policy in self.filters:
            policy._pair_cache.clear()  # Replay-day expiration, never wall-clock TTL.
            filtered = policy.filter_pairlist(pairs, {})
            rejected = set(pairs) - set(filtered)
            for pair in pairs:
                value = policy._pair_cache.get(pair)
                metrics.setdefault(pair, {})[policy.name] = (
                    float(value) if value is not None else None
                )
                if pair in rejected:
                    reasons[pair] = policy.name
            pairs = filtered
        result = {
            "pairs": pairs[:80],
            "admitted": len(pairs) >= 80,
            "eligible_before_risk": len(ranks),
            "eligible_after_risk": len(pairs),
            "latest_input_close": max(input_clocks).isoformat(),
            "ranked_quote_volume": dict(ranks),
            "risk_exclusions": reasons,
            "risk_values": metrics,
        }
        self.cached_day, self.cached_result = day, result
        return result


class HistoricalVolumePairList(IPairList):
    is_pairlist_generator = True
    supports_backtesting = SupportsBacktesting.YES

    @property
    def needstickers(self):
        return False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self._config["runmode"] != RunMode.BACKTEST:
            raise OperationalException("Historical blacklist policy is backtest-only")
        blob = Path(self._pairlistconfig["schedule"]).read_bytes()
        if hashlib.sha256(blob).hexdigest() != self._pairlistconfig["sha256"]:
            raise OperationalException("Frozen blacklist schedule differs")
        self.payload = json.loads(blob)
        self.book = RuleBook(self.payload, self._exchange, self._pairlistmanager, self._config)

    def short_desc(self):
        return "Historical structural eligibility and native risk filters"

    def gen_pairlist(self, tickers):
        clock = self._pairlistmanager._dataprovider.backtest_time
        if clock is None:
            return self.payload["union"].copy()
        row = self.book.select(clock)
        day = clock.date().isoformat()
        if not row["admitted"] or row["pairs"] != self.payload["days"][day]["pairs"]:
            raise OperationalException(
                "Actual native historical rule pool differs or has fewer than80"
            )
        return row["pairs"].copy()
