"""Use native compact indicator storage consistently in replay and forward operation."""

from NostalgiaForInfinityX4 import NostalgiaForInfinityX4

from freqtrade.data.converter import reduce_dataframe_footprint


class NFI2023OneXCompact(NostalgiaForInfinityX4):
    """Fixed 1x leverage; native float32 indicators in all modes, full-precision OHLCV."""

    futures_mode_leverage = 1.0
    futures_mode_leverage_rebuy_mode = 1.0

    def populate_indicators(self, dataframe, metadata):
        return reduce_dataframe_footprint(super().populate_indicators(dataframe, metadata))
