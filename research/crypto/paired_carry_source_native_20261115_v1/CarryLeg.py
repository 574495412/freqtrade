"""Precommitted quantity, one market entry; all orders/risks remain native."""
import math

import pandas as pd
from freqtrade.enums import RunMode
from freqtrade.strategy import IStrategy


class CarrySpot(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = '5m'
    startup_candle_count = 1
    minimal_roi = {}
    stoploss = -.99
    position_adjustment_enable = False
    order_types = {'entry': 'market', 'exit': 'market', 'stoploss': 'market',
                   'stoploss_on_exchange': False}

    def ft_bot_start(self, **kwargs):
        self.bot_start()
        super().ft_bot_start(**kwargs)
        if not hasattr(self, 'research_trace'):
            raise ValueError('Carry source initialization did not complete')

    def bot_start(self, **kwargs):
        if (self.config.get('runmode') != RunMode.BACKTEST or not self.config.get('dry_run')
                or not self.config.get('research_carry_leg')
                or self.config['max_open_trades'] != 1):
            raise ValueError('Segregated backtest carry leg only')
        self._fatal_error = None
        self.research_trace = {'budgets': [], 'confirmed': []}

    def populate_indicators(self, dataframe, metadata):
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe['enter_long'] = 0; dataframe['enter_short'] = 0
        flag = 'enter_short' if self.can_short else 'enter_long'
        dataframe.loc[dataframe.date.eq(pd.Timestamp('2025-01-01T00:00Z')), flag] = 1
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe['exit_long'] = 0; dataframe['exit_short'] = 0
        return dataframe

    def leverage(self, *args, **kwargs):
        return 1.

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake,
                            min_stake, max_stake, leverage, entry_tag, side, **kwargs):
        q = self.config['research_quantity']
        stake = q * current_rate
        admitted = (math.isfinite(stake) and stake > 0 and leverage == 1
                    and min_stake is not None and min_stake <= stake <= max_stake
                    and side == ('short' if self.can_short else 'long'))
        self.research_trace['budgets'].append(dict(pair=pair, at=str(current_time),
            quantity=q, rate=current_rate, budget=max_stake, min_stake=min_stake,
            proposed_stake=proposed_stake, requested=stake, admitted=admitted))
        if not admitted:
            self._fatal_error = 'Precommitted quantity cannot fit native leg wallet/minimum'
            return 0
        return stake

    def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force,
                            current_time, entry_tag, side, **kwargs):
        valid = (self._fatal_error is None and abs(amount-self.config['research_quantity']) < 1e-12
                 and current_time == pd.Timestamp('2025-01-01T00:05Z')
                 and side == ('short' if self.can_short else 'long'))
        if not valid:
            self._fatal_error = 'Actual native quantity/clock/side differs from frozen carry intent'
            return False
        self.research_trace['confirmed'].append(dict(pair=pair, at=str(current_time),
            quantity=amount, rate=rate, side=side))
        return True


class CarryShort(CarrySpot):
    can_short = True
