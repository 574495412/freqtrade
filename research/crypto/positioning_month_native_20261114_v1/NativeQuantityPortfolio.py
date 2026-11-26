"""Local native quantity callbacks reused from frozen TSMOM research; new targets supplied separately."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from NativeAllocations import marked_budget

from freqtrade.data.history import load_pair_history
from freqtrade.enums import CandleType
from freqtrade.persistence import LocalTrade,Trade
from freqtrade.strategy import IStrategy


class NativeQuantityPortfolio(IStrategy):
    INTERFACE_VERSION=3
    timeframe='1h'
    startup_candle_count=72
    can_short=True
    minimal_roi={}
    stoploss=-.99
    use_exit_signal=True
    position_adjustment_enable=True
    max_entry_position_adjustment=-1
    order_types={'entry':'market','exit':'market','stoploss':'market','stoploss_on_exchange':False}
    allocation_mode=None

    def populate_indicators(self,dataframe,metadata):
        values=self.research_features[metadata['pair']].set_index('decision_date')
        days=pd.DatetimeIndex(dataframe.date.dt.floor('D'))
        for field in ['target_weight','source_signal','source_volatility']:dataframe[field]=values[field].reindex(days).to_numpy()
        return dataframe

    def populate_entry_trend(self,dataframe,metadata):
        pulse=dataframe.date.dt.hour.eq(0)
        dataframe['enter_long']=(pulse & dataframe.target_weight.gt(0)).astype(int)
        dataframe['enter_short']=(pulse & dataframe.target_weight.lt(0)).astype(int)
        return dataframe

    def populate_exit_trend(self,dataframe,metadata):
        pulse=dataframe.date.dt.hour.eq(0)
        dataframe['exit_long']=(pulse & dataframe.target_weight.le(0)).astype(int)
        dataframe['exit_short']=(pulse & dataframe.target_weight.ge(0)).astype(int)
        dataframe['exit_tag']='source_daily_direction'
        return dataframe

    def leverage(self,pair,current_time,current_rate,proposed_leverage,max_leverage,entry_tag,side,**kwargs):
        if max_leverage<3:raise ValueError('Frozen threefold margin contract unavailable')
        return 3.

    def context(self,current_time):
        stamp=pd.Timestamp(current_time)
        if stamp.hour!=1 or stamp.minute or stamp.second:return None
        if self._context is not None and self._context['clock']==stamp:return self._context
        trades=Trade.get_trades_proxy(is_open=True)
        marks={p:float(s.loc[stamp]) for p,s in self._marks.items()}
        equity=marked_budget(float(self.config['dry_run_wallet']),LocalTrade.bt_total_profit,trades,marks)
        quantities={};prices={};minimums={}
        for pair in self.config['exchange']['pair_whitelist']:
            rate=float(self._prices[pair].loc[stamp]);weight=float(self.research_weights.at[stamp.floor('D'),pair])
            amount=self.dp._exchange.amount_to_precision(pair,.99*equity*abs(weight)/rate)
            minimum=self.dp._exchange.get_min_pair_stake_amount(pair,rate,self.stoploss,3.) or 0.
            if amount*rate/3<minimum:amount=0.
            quantities[pair]=amount;prices[pair]=rate;minimums[pair]=minimum
        self._context={'clock':stamp,'equity':equity,'quantities':quantities,'prices':prices,'minimums':minimums}
        self.research_trace['budgets'].append({'clock':stamp.isoformat(),'native_marked_equity':equity,'target_quantities':quantities})
        return self._context

    def bot_loop_start(self,current_time,**kwargs):self.context(current_time)

    def custom_stake_amount(self,pair,current_time,current_rate,proposed_stake,min_stake,max_stake,leverage,entry_tag,side,**kwargs):
        context=self.context(current_time)
        if context is None:return 0.
        budget=min(context['quantities'][pair]*current_rate/3,max_stake)
        self.research_trace['attempts'].append({'pair':pair,'clock':pd.Timestamp(current_time).isoformat(),'budget':budget,'minimum':min_stake,'side':side})
        return budget if budget>0 and budget>=(min_stake or 0) else 0.

    def confirm_trade_entry(self,pair,order_type,amount,rate,time_in_force,current_time,entry_tag,side,**kwargs):
        context=self.context(current_time)
        if context is None:return False
        existing=[t for t in Trade.get_trades_proxy(is_open=True) if t.pair==pair]
        held=existing[0].amount if existing and existing[0].trade_direction==side else 0.
        if held+amount>context['quantities'][pair]+1e-10:return False
        self.research_trace['accepted'].append({'pair':pair,'clock':pd.Timestamp(current_time).isoformat(),'amount':amount,'rate':rate,'prior_quantity':held,'target_quantity':context['quantities'][pair],'side':side})
        return True

    def adjust_trade_position(self,trade,current_time,current_rate,current_profit,min_stake,max_stake,**kwargs):
        context=self.context(current_time)
        if context is None:return None
        key=(trade.id,context['clock'])
        if key in self._attempted:return None
        weight=float(self.research_weights.at[context['clock'].floor('D'),trade.pair])
        if int(np.sign(weight))!=(-1 if trade.is_short else 1):return None
        target=context['quantities'][trade.pair];delta=target-trade.amount
        if abs(delta)<1e-12:return None
        self._attempted.add(key)
        if target==0:stake=-trade.stake_amount
        elif delta<0:stake=delta*trade.stake_amount/trade.amount
        else:stake=min(delta*current_rate/3,max_stake)
        if stake>0 and stake<(min_stake or 0):return None
        # Native full exit is allowed; tiny partial deltas stay as tracking error.
        if stake<0 and target>0 and abs(stake)<context['minimums'][trade.pair]:return None
        self.research_trace['adjustments'].append({'pair':trade.pair,'clock':context['clock'].isoformat(),'old_quantity':trade.amount,'target_quantity':target,'requested_stake_delta':stake})
        return stake,'source_daily_quantity'

