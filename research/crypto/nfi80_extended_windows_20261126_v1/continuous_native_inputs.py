"""Bind frozen shards to native candle, indicator and valuation reads."""

from collections import Counter, OrderedDict
from collections.abc import Mapping
from pathlib import Path

import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read


class RecentFrames(OrderedDict):
    def __init__(self, maximum):
        super().__init__()
        self.maximum = maximum

    def __getitem__(self, key):
        result = super().__getitem__(key)
        self.move_to_end(key)
        return result

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.move_to_end(key)
        if len(self) > self.maximum:
            self.popitem(last=False)


class NativeMainCandles(Mapping):
    """Load native-cleaned main-timeframe frames with bounded retention."""

    def __init__(self, source, kwargs, counts):
        self.source = source
        self.kwargs = dict(kwargs)
        if kwargs.get("timerange") is not None:
            self.kwargs["timerange"] = kwargs["timerange"].copy()
        self.order = tuple(kwargs["pairs"])
        self.members = set(self.order)
        self.cache = RecentFrames(2)
        self.counts = counts

    def __getitem__(self, pair):
        if pair not in self.members:
            raise KeyError(pair)
        if pair not in self.cache:
            data = self.source(**{**self.kwargs, "pairs": [pair]})
            if pair not in data or data[pair].empty:
                raise ValueError(f"Missing admitted main-timeframe history for {pair}")
            self.cache[pair] = data[pair]
            self.counts["uncached_native_main_pair_loads"] += 1
        return self.cache[pair]

    def __iter__(self):
        return iter(self.order)

    def __len__(self):
        return len(self.order)

    def __contains__(self, pair):
        return pair in self.members


class ObserverPrices(Mapping):
    """Expose the same raw OPEN series the original observer uses."""

    def __init__(self, data, kind, pairs, bounds, counters):
        from freqtrade.enums import CandleType

        self.data = data
        self.kind = kind
        timeframe, mode = kind.split("-", 1)
        self.timeframe = timeframe
        self.candle_type = CandleType(mode)
        self.pairs = {pair: candle_filename(pair, kind) for pair in pairs}
        self.bounds = bounds
        self.counters = counters
        self.cache = RecentFrames(20)
        for key in self.pairs.values():
            assert key in data.shards

    def __contains__(self, pair):
        return pair in self.pairs

    def __iter__(self):
        return iter(self.pairs)

    def __len__(self):
        return len(self.pairs)

    def __getitem__(self, pair):
        if pair not in self.cache:
            if pair not in self.pairs:
                raise KeyError(pair)
            # The storage layer returns raw candles with native column
            # normalization, before candle cleaning or gap filling. Observer cash
            # callbacks still enforce exact held-position marks and fees.
            frame = self.data._ohlcv_load(pair, self.timeframe, None, self.candle_type)
            selected = frame.set_index("date").loc[slice(*self.bounds), "open"].copy()
            self.cache[pair] = selected
            self.counters[f"observer_{self.kind}_series"] += 1
        return self.cache[pair]


def candle_filename(pair, kind):
    stem = pair.replace("/", "_").replace(":", "_")
    return f"futures/{stem}-{kind}.feather"


class NativeShardBinding:
    """Process-local binding for the hash-pinned Freqtrade history functions."""

    def __init__(self, plan, timeframe):
        from freqtrade.data import history
        from freqtrade.data.history import history_utils
        from freqtrade.enums import CandleType
        from research.crypto.nfi80_extended_windows_20261126_v1.sharded_feather import (
            ShardedFeatherDataHandler,
        )

        self.history = history
        self.history_utils = history_utils
        self.datadir = Path(plan["datadir"]).resolve()
        self.handler = ShardedFeatherDataHandler(self.datadir, plan["data_receipts"])
        self.main = timeframe
        self.futures = CandleType.FUTURES
        self.counts = Counter()
        self.source_load = history.load_data
        self.source_handler = history_utils.get_datahandler
        self.laziness = None

    def install(self):
        self.history.load_data = self.load_data
        self.history_utils.get_datahandler = self.find_handler

    def find_handler(self, datadir, data_format=None, data_handler=None):
        if Path(datadir).resolve() != self.datadir or data_handler is not None:
            return self.source_handler(datadir, data_format, data_handler)
        if data_format not in (None, "feather"):
            raise ValueError("Unexpected input format for frozen continuous candle shards")
        self.counts["native_shard_handler_resolutions"] += 1
        return self.handler

    def load_data(self, *args, **kwargs):
        if (not args and kwargs.get("datadir") and Path(kwargs["datadir"]).resolve() == self.datadir
                and kwargs.get("timeframe") == self.main and kwargs.get("candle_type") == self.futures
                and len(kwargs["pairs"]) > 1):
            if self.laziness is not None:
                raise ValueError("Unexpected duplicate native main-timeframe load")
            self.laziness = NativeMainCandles(self.source_load, kwargs, self.counts)
            return self.laziness
        return self.source_load(*args, **kwargs)

    def bind_provider(self, provider):
        # These cache values are immutable historical candles. A missing key
        # uses DataProvider's normal candle reader with the same world clocks.
        old = provider._DataProvider__cached_pairs_backtesting
        bounded = RecentFrames(24)
        bounded.update(old)
        provider._DataProvider__cached_pairs_backtesting = bounded

    def observer(self, observer, account, plan, output):
        initialized = observer(account, {**plan, "observer_pairs": []}, output)
        bounds = (plan["observation_start"], plan["observation_end"])
        initialized.prices = ObserverPrices(self.handler, "5m-futures", plan["observer_pairs"], bounds, self.counts)
        initialized.marks = ObserverPrices(self.handler, "5m-mark", plan["observer_pairs"], bounds, self.counts)
        return initialized

    def audit(self):
        if self.laziness is None or not self.handler.checked_parts:
            raise ValueError("The native replay did not use the registered shard reader")
        return {"counts": dict(self.counts), "verified_shard_files_loaded": len(self.handler.checked_parts),
                "main_raw_cache_limit": self.laziness.cache.maximum,
                "historical_indicator_cache_limit": 24, "observer_cache_per_type": 20, "orders_enabled": False}


class IndependentShardRead:
    """Read original shard ranges directly for the independent cash auditor."""

    def __init__(self, manifest):
        self.receipt = manifest
        self.files = read(checked(manifest))["files"]
        self.calls = Counter()

    def __call__(self, pair, kind):
        key = candle_filename(pair, kind)
        periods = []
        for record in self.files[key]:
            frame = pd.read_feather(checked(record)).set_index("date")
            start, stop = record["start_inclusive"], pd.Timestamp(record["stop_exclusive"])
            chosen = frame.loc[frame.index < stop]
            if start is not None:
                chosen = chosen.loc[chosen.index >= pd.Timestamp(start)]
            periods.append(chosen)
        values = pd.concat(periods)
        assert values.index.is_unique and values.index.is_monotonic_increasing
        self.calls[kind] += 1
        return values.reset_index()
