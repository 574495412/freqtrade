"""Backtest-only native IStrategy; mathematical source rules, local account."""

import numpy as np
import pandas as pd
from DualRules import completed_states

from freqtrade.enums import RunMode
from freqtrade.strategy import IStrategy


class DualPortfolio(IStrategy):
    INTERFACE_VERSION=3
    timeframe='1h';startup_candle_count=600;can_short=False
    minimal_roi={};stoploss=-.99;use_exit_signal=True
    position_adjustment_enable=False
    order_types={'entry':'market','exit':'market','stoploss':'market','stoploss_on_exchange':False}
    mode=None

    def bot_start(self,**kwargs):
        if self.config.get('runmode')!=RunMode.BACKTEST:raise ValueError('Backtest-only research, no services')
        pair='BTC/USDT:USDT'
        if self.config['exchange']['pair_whitelist']!=[pair]:raise ValueError('Frozen instrument changed')
        candles=self.dp.get_pair_dataframe(pair,'1h','futures')
        candles=candles.loc[candles.date.lt(pd.Timestamp('2026-09-01T00:00Z'))]
        fields,daily=completed_states(candles,self.mode)
        self.research_features={pair:fields}
        selected=fields.clock.between(pd.Timestamp('2025-01-01T00:00Z'),pd.Timestamp('2026-08-31T23:00Z'))
        weights=fields.loc[selected].set_index('clock')[['target_weight']].rename(columns={'target_weight':pair})
        self.research_weights=weights.astype(float)
        self._targets={str(d):{pair:int(v)} for d,v in weights[pair].items()}
        self.research_allocation_diagnostics=dict(mode=self.mode,full_hour_rows=len(weights),
            source_anchor='Latest completed daily open',source_days=4,k1=.5,k2=.5,
            sizing='Native99percent unlimited; actual quantities retained until exit',
            signal_field='Source renewal/cancel event, not a cross-sectional alpha',
            source_volatility_field='Not applicable/NaN',source_cash_account_replication=False,
            historical_market_master_pit=False,daily_rows=len(daily))
        self.research_trace={'accepted':[]}

    def populate_indicators(self,dataframe,metadata):
        fields=self.research_features[metadata['pair']].set_index('date')
        for column in ['target_weight','source_signal','source_volatility']:
            dataframe[column]=fields[column].reindex(pd.DatetimeIndex(dataframe.date)).to_numpy()
        return dataframe

    def populate_entry_trend(self,dataframe,metadata):
        execute=dataframe.date+pd.Timedelta(hours=1)
        permitted=~execute.dt.hour.isin([0,8,16])&execute.ge(pd.Timestamp('2025-01-01T00:00Z'))
        dataframe['enter_long']=(permitted&dataframe.target_weight.gt(0)&dataframe.volume.gt(0)).astype(int)
        dataframe['enter_short']=0
        return dataframe

    def populate_exit_trend(self,dataframe,metadata):
        permitted=~(dataframe.date+pd.Timedelta(hours=1)).dt.hour.isin([0,8,16])
        dataframe['exit_long']=(permitted&dataframe.target_weight.le(0)).astype(int)
        dataframe['exit_short']=0;dataframe['exit_tag']='completed_state_false'
        return dataframe

    def leverage(self,**kwargs):return 1.

    def custom_stake_amount(self,pair,current_time,current_rate,proposed_stake,min_stake,max_stake,leverage,entry_tag,side,**kwargs):
        return proposed_stake if proposed_stake>=(min_stake or 0) and proposed_stake<=max_stake else 0.

    def confirm_trade_entry(self,pair,order_type,amount,rate,time_in_force,current_time,entry_tag,side,**kwargs):
        when=pd.Timestamp(current_time)
        if when.minute or when.second or when.hour in [0,8,16] or when<pd.Timestamp('2025-01-01T00:00Z'):return False
        self.research_trace['accepted'].append(dict(clock=str(when),amount=amount,rate=rate,pair=pair))
        return True


class SourceDualBTC(DualPortfolio):mode='dual'
class SmaPriceBTC(DualPortfolio):mode='sma'
class HoldBTC(DualPortfolio):mode='hold'
