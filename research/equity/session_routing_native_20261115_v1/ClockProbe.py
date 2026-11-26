"""Diagnostic flags for signal-custody checks only, never a research alpha."""
from freqtrade.strategy import IStrategy


class ClockProbe(IStrategy):
    timeframe = '1d'
    can_short = False
    stoploss = -.99
    minimal_roi = {}

    def populate_indicators(self, dataframe, metadata):
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe['enter_long'] = (dataframe.close > dataframe.open).astype(int)
        dataframe['enter_tag'] = dataframe.date.dt.strftime('%Y-%m-%d')
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe['exit_long'] = (dataframe.close <= dataframe.open).astype(int)
        dataframe['exit_tag'] = dataframe.date.dt.strftime('%Y-%m-%d')
        return dataframe

    def ft_bot_start(self, **kwargs):
        raise ValueError('Clock-custody probe cannot start a financial account')
