"""Local capital contract and causal combination of pinned public source classes.

Source002 and ADX formulas/exits/ROI/stops are inherited unchanged. The regime
combination filters only entries using the last completed native1hour signal.
"""
import math

import pandas as pd
from freqtrade.enums import RunMode
from freqtrade.strategy import IStrategy, merge_informative_pair

from ADXMomentum import ADXMomentum
from Strategy002 import Strategy002


class _BoundedCapital:
    def ft_bot_start(self, **kwargs):
        if (self.config.get('runmode') != RunMode.BACKTEST or self.config.get('dry_run') is not True
                or self.config.get('research_source_pair') is not True
                or self.config.get('trading_mode') != 'futures'
                or self.config.get('margin_mode') != 'isolated'
                or self.config.get('max_open_trades') != 8
                or self.config.get('position_adjustment_enable')):
            raise ValueError('Isolated fixed source research account required')
        self._fatal_error = None
        self.research_trace = {'budgets': [], 'confirmed': []}
        self._latest_budget = {}
        return super().ft_bot_start(**kwargs)

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake,
                            min_stake, max_stake, leverage, entry_tag, side, **kwargs):
        if (side != 'long' or leverage != 1 or min_stake is None
                or not all(math.isfinite(v) and v >= 0 for v in (proposed_stake,min_stake,max_stake))):
            self._fatal_error = 'Unknown native stake specification or unexpected direction/leverage'
            return 0
        budget = min(proposed_stake,max_stake)
        admitted = budget >= min_stake
        self.research_trace['budgets'].append(dict(pair=pair,at=str(current_time),
            proposed=proposed_stake,minimum=min_stake,maximum=max_stake,
            budget=budget,admitted=admitted))
        self._latest_budget[(pair,pd.Timestamp(current_time))] = budget if admitted else 0
        return budget if admitted else 0

    def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force,
                            current_time, entry_tag, side, **kwargs):
        budget = self._latest_budget.get((pair,pd.Timestamp(current_time)))
        if budget is None or budget <= 0 or side != 'long' or amount*rate > budget+1e-7:
            self._fatal_error = 'Entry exceeds its actual native per-slot budget'
            return False
        self.research_trace['confirmed'].append(dict(pair=pair,at=str(current_time),
            quantity=amount,rate=rate,budget=budget,order_type=order_type))
        return True


class SourceReversal(_BoundedCapital, Strategy002):
    pass


class SourceADX(_BoundedCapital, ADXMomentum):
    pass


class ReversalInADXTrend(SourceReversal):
    def informative_pairs(self):
        return [(pair,'1h') for pair in self.config['exchange']['pair_whitelist']]

    def populate_indicators(self, dataframe, metadata):
        result = super().populate_indicators(dataframe,metadata)
        higher = self.dp.get_pair_dataframe(metadata['pair'],'1h','futures')
        source = ADXMomentum(self.config)
        higher = source.populate_indicators(higher.copy(),metadata)
        higher = source.populate_entry_trend(higher,metadata)
        higher['adx_source_long'] = higher['enter_long'].fillna(0).eq(1).astype(int)
        keep = ['date','adx','plus_di','minus_di','mom','adx_source_long']
        return merge_informative_pair(result,higher[keep],'5m','1h',ffill=True)

    def populate_entry_trend(self, dataframe, metadata):
        result = super().populate_entry_trend(dataframe,metadata)
        known = pd.to_datetime(result['date_1h'],utc=True)+pd.Timedelta(hours=1)
        close = pd.to_datetime(result.date,utc=True)+pd.Timedelta(minutes=5)
        if (known.dropna() > close.loc[known.notna()]).any():
            raise ValueError('Informative source bar not complete at base decision close')
        result['enter_long'] = (result['enter_long'].fillna(0).eq(1)
            & result['adx_source_long_1h'].fillna(0).eq(1)).astype(int)
        return result


class HoldEight(_BoundedCapital, IStrategy):
    INTERFACE_VERSION = 3
    timeframe = '5m'
    minimal_roi = {}
    stoploss = -.99
    trailing_stop = False
    position_adjustment_enable = False
    order_types = {'entry':'limit','exit':'limit','stoploss':'market','stoploss_on_exchange':False}

    def populate_indicators(self, dataframe, metadata):
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        # One original intent at the first target five-minute opening. No
        # repeated retry of rejected minimum-sized members in later months.
        dataframe['enter_long'] = dataframe.date.eq(pd.Timestamp('2025-01-01T00:00Z')).astype(int)
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe['exit_long'] = 0
        return dataframe
