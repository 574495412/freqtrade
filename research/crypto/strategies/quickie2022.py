"""Author's fixed 2022 5m momentum baseline with explicit warmup and no DCA."""

from Quickie import Quickie


class Quickie2022(Quickie):
    startup_candle_count = 400
    can_short = False
    position_adjustment_enable = False
    process_only_new_candles = True
