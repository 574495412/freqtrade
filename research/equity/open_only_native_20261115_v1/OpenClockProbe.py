"""Synthetic diagnostic flags, not a financial candidate."""
from freqtrade.enums import RunMode
from freqtrade.strategy import IStrategy


class OpenClockProbe(IStrategy):
    timeframe = '1d'
    can_short = False
    stoploss = -.99
    minimal_roi = {}
    trailing_stop = False
    position_adjustment_enable = False
    order_types = {'entry': 'market', 'exit': 'market', 'stoploss': 'market',
                   'stoploss_on_exchange': False}

    def ft_bot_start(self, **kwargs):
        if (self.config.get('runmode') != RunMode.BACKTEST or not self.config.get('dry_run')
                or self.config['exchange']['pair_whitelist'] not in [['ABC/USD'], ['ABC/HKD']]
                or self.config.get('synthetic_equity_fixture') is not True):
            raise ValueError('Only isolated ABC synthetic backtests allowed')
        return super().ft_bot_start(**kwargs)

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
