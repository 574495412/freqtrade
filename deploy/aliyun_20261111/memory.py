"""Return unreachable temporaries and unused glibc pages after native analysis."""

import ctypes
import gc
import sys
import time
from functools import lru_cache
from pathlib import Path


def resident_bytes():
    if sys.platform != "linux":
        return None
    fields = Path("/proc/self/status").read_text().splitlines()
    return int(next(line.split()[1] for line in fields if line.startswith("VmRSS:"))) * 1024


@lru_cache(maxsize=1)
def trim_function():
    if sys.platform != "linux":
        return None
    try:
        function = ctypes.CDLL(None).malloc_trim
    except AttributeError:
        return None
    function.argtypes = [ctypes.c_size_t]
    function.restype = ctypes.c_int
    return function


def reclaim_heap():
    started = time.monotonic()
    before = resident_bytes()
    collected = gc.collect()
    trim = trim_function()
    trimmed = bool(trim(0)) if trim is not None else False
    return {
        "rss_before_bytes": before, "rss_after_bytes": resident_bytes(),
        "unreachable_objects_collected": collected, "glibc_trim_released_pages": trimmed,
        "elapsed_seconds": round(time.monotonic() - started, 4),
    }


def analysis_required(strategy, pairs):
    """Determine allocation work without changing the native skip/analysis decision."""
    if not strategy.process_only_new_candles:
        return True
    for pair in pairs:
        raw = strategy.dp.ohlcv(pair, strategy.timeframe, copy=False)
        cached, _ = strategy.dp.get_analyzed_dataframe(pair, strategy.timeframe)
        if not raw.empty and (cached.empty or raw.iloc[-1]["date"] != cached.iloc[-1]["date"]):
            return True
    return False
