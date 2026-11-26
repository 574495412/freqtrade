"""Local categorical strategy on the unchanged native FreqAI feature workflow."""
import numpy as np
import pandas as pd
from FreqaiExampleStrategy import FreqaiExampleStrategy
from ModelRules import intents,targets

from freqtrade.enums import RunMode


class FreqAIState(FreqaiExampleStrategy):
    timeframe='1h';startup_candle_count=80
    position_adjustment_enable=False
    order_types={'entry':'market','exit':'market','stoploss':'market','stoploss_on_exchange':False}

    def load_freqAI_model(self):
        if self.config.get('runmode')!=RunMode.BACKTEST:raise ValueError('No FreqAI live/dry-run services')
        return super().load_freqAI_model()

    def bot_start(self,**kwargs):
        if self.config['exchange']['pair_whitelist']!=['BTC/USDT:USDT']:raise ValueError('Frozen BTC instrument changed')
        self.research_features={};self.research_trace={'accepted':[]}

    def set_freqai_targets(self,dataframe,metadata,**kwargs):
        dataframe['&-direction']=targets(dataframe)
        return dataframe

    def populate_indicators(self,dataframe,metadata):
        f=self.freqai.start(dataframe,metadata,self)
        start=pd.Timestamp(self.config.get('research_scope_start','2025-01-01T00:00Z'))
        f['target_weight']=intents(f,start)
        # Undo only the native pre-window label placeholder for lossless export.
        # No warmup label is a known class, and it cannot create a position.
        f.loc[f.date.lt(start),'&-direction']=pd.NA
        f['&-direction']=f['&-direction'].astype('str')
        f['source_signal']=f.up-f.down;f['source_volatility']=np.nan
        f['clock']=f.date+pd.Timedelta(hours=1);f['decision_date']=f.clock
        self.research_features={metadata['pair']:f.copy()}
        window=f.clock.between(start,pd.Timestamp(self.config.get('research_scope_end','2026-08-31T23:00Z')))
        weights=f.loc[window].set_index('clock')[['target_weight']].rename(columns={'target_weight':metadata['pair']})
        if start not in weights.index:weights.loc[start]=0
        self.research_weights=weights.sort_index().astype(float)
        if not self.config.get('research_component_only',False) and len(weights)!=14592:
            raise ValueError('Incomplete full-window native target calendar')
        self._targets={str(d):{metadata['pair']:int(v)} for d,v in self.research_weights.iloc[:,0].items()}
        self.research_allocation_diagnostics=dict(freqaimodel=self.config['freqaimodel'],
            prediction_rows=int(f.date.ge(start).sum()),known_prediction_rows=int((f.date.ge(start)&f.do_predict.eq(1)).sum()),
            local_categorical_target=True,source_rolling_training=True,source_risk_rules=True,
            not_cross_sectional_alpha=True,source_volatility_field='Not applicable/NaN')
        return f

    def populate_entry_trend(self,f,metadata):
        execute=f.date+pd.Timedelta(hours=1);permitted=~execute.dt.hour.isin([0,8,16])&execute.ge(pd.Timestamp('2025-01-01T00:00Z'))
        f['enter_long']=(permitted&f.target_weight.gt(0)&f.volume.gt(0)).astype(int)
        f['enter_short']=(permitted&f.target_weight.lt(0)&f.volume.gt(0)).astype(int)
        f['enter_tag']='rolling_native_classification'
        return f

    def populate_exit_trend(self,f,metadata):
        permitted=~(f.date+pd.Timedelta(hours=1)).dt.hour.isin([0,8,16])
        f['exit_long']=(permitted&f.target_weight.le(0)).astype(int)
        f['exit_short']=(permitted&f.target_weight.ge(0)).astype(int)
        f['exit_tag']='native_probability_state'
        return f

    def leverage(self,**kwargs):return 1.

    def custom_stake_amount(self,pair,current_time,current_rate,proposed_stake,min_stake,max_stake,leverage,entry_tag,side,**kwargs):
        return proposed_stake if (min_stake or 0)<=proposed_stake<=max_stake else 0.

    def confirm_trade_entry(self,pair,order_type,amount,rate,time_in_force,current_time,entry_tag,side,**kwargs):
        clock=pd.Timestamp(current_time)
        if clock.minute or clock.second or clock.hour in [0,8,16] or clock<pd.Timestamp('2025-01-01T00:00Z'):return False
        accepted=super().confirm_trade_entry(pair,order_type,amount,rate,time_in_force,current_time,entry_tag,side,**kwargs)
        if accepted:self.research_trace['accepted'].append(dict(clock=str(clock),amount=amount,rate=rate,pair=pair,side=side))
        return accepted


class RollingRF(FreqAIState):pass
class RollingPrior(FreqAIState):pass
