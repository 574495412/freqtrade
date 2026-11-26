"""Local one-minute interpretations of DOI10.1016/j.heliyon.2023.e14236.

Paper minute-label convention is unresolved; neither interpretation is certified
as the original implementation. Native next-bar execution is retained.
"""
import pandas as pd
from freqtrade.strategy import IStrategy


class BoundaryRules(IStrategy):
    INTERFACE_VERSION = 3
    can_short = False
    timeframe = '1m'
    startup_candle_count = 2
    process_only_new_candles = True
    minimal_roi = {}
    stoploss = -0.99
    trailing_stop = False
    position_adjustment_enable = False
    use_exit_signal = True
    order_types = {'entry': 'market', 'exit': 'market', 'stoploss': 'market', 'stoploss_on_exchange': False}
    entry_phase = 0
    passive = False

    def populate_indicators(self, dataframe, metadata):
        # Row T closes at T+1m. Only its already-known calendar is used to
        # schedule the next native open. No price-return threshold is fitted.
        next_open = dataframe.date + pd.Timedelta(minutes=1)
        dataframe['scheduled_entry'] = next_open.dt.minute.mod(15).eq(self.entry_phase)
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe['enter_long'] = 1 if self.passive else dataframe.scheduled_entry.astype(int)
        dataframe['enter_tag'] = 'constant' if self.passive else f'phase_{self.entry_phase}'
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe['exit_long'] = 0
        return dataframe

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if not self.passive and current_time >= trade.open_date_utc + pd.Timedelta(minutes=1):
            return 'one_minute_end'
        return None


class BoundaryStartMinute(BoundaryRules):
    entry_phase = 0


class BoundaryEndMinute(BoundaryRules):
    entry_phase = 14


class ConstantMinuteControl(BoundaryRules):
    passive = True
