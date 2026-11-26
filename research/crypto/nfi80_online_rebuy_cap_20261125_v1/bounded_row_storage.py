"""Preserve exact native row storage while releasing bounded frame caches."""

from research.crypto.nfi80_historical_universe_20261109.v003.row_storage import (
    LazyTrimMapping, RowBlocks,
)


def release_consumed(mapping, pair, cache_root):
    return mapping.discard_generated_cache(pair, cache_root)
