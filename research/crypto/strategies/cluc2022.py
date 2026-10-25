"""Pin the author's 5m Cluc strategy to a no-DCA, 1x native research account."""

from ClucMay72018 import ClucMay72018


class Cluc2022(ClucMay72018):
    startup_candle_count = 200
    can_short = False
    position_adjustment_enable = False
    process_only_new_candles = True
