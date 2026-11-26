"""Keep the original native row blocks and release only indicator handles."""

from research.crypto.nfi80_historical_universe_20261109.v003.row_storage import RowBlocks


def release_consumed(mapping, pair, cache_root):
    return mapping.consume(pair)
