"""Local source-motivated monthly positioning opportunity, native finances only."""
from pathlib import Path
import numpy as np
import pandas as pd
from NativeQuantityPortfolio import NativeQuantityPortfolio
from Signals import signal_frames,opportunities
from freqtrade.enums import RunMode

class PositioningPortfolio(NativeQuantityPortfolio):
    can_short=False
    allocation_mode=None
    def bot_start(self,**kwargs):
        if self.config.get('runmode')!=RunMode.BACKTEST or not self.config.get('dry_run'):
            raise ValueError('Frozen isolated native historical research only')
        pairs=self.config['exchange']['pair_whitelist']
        if pairs!=['BTC/USDT:USDT','ETH/USDT:USDT','SOL/USDT:USDT']:
            raise ValueError('Frozen source three-coin universe')
        study=Path(self.config['research_study']);output=Path(self.config['research_model_output'])
        frames,capsule=signal_frames(study,output)
        days=pd.date_range('2025-01-01','2026-08-31',tz='UTC',freq='D')
        self.research_features={};self.research_opportunities={};self.research_weights=pd.DataFrame(index=days)
        self.research_allocation_diagnostics={'mode':self.allocation_mode,'source_gross_event_study_not_reproduced':True,
            'cash_policy':'known opportunity union; never open opportunity on missing rank','unknown_daily_fields':capsule['unknown_daily_fields']}
        for pair in pairs:
            symbol=pair.split('/')[0]+'USDT'
            decision=opportunities(study,frames[symbol],'crowd'if self.allocation_mode=='constant'else self.allocation_mode)
            self.research_opportunities[pair]=decision
            weight=np.full(len(days),1/3)if self.allocation_mode=='constant'else decision.active.to_numpy(dtype=float)/3
            self.research_weights[pair]=weight
            # Keep unknown ranks in the actual native feature path as NaN.
            # Allocation is separately governed by the explicit expiry policy.
            self.research_features[pair]=pd.DataFrame({'decision_date':days,'target_weight':weight,
               'source_signal':decision['rank'].to_numpy(),'source_volatility':np.zeros(len(days))})
        self._prices={p:self.dp.get_pair_dataframe(p,'1h','futures').set_index('date').open for p in pairs}
        self._marks={p:self.dp.get_pair_dataframe(p,'1h','mark').set_index('date').open for p in pairs}
        self._targets={d.strftime('%Y-%m-%d'):{p:int(np.sign(self.research_weights.at[d,p]))for p in pairs}for d in days}
        self.research_trace={'attempts':[],'accepted':[],'adjustments':[],'budgets':[]}
        self._context=None;self._attempted=set()
    def bot_loop_start(self,current_time,**kwargs):
        self.position_adjustment_enable=pd.Timestamp(current_time).hour==1
        return super().bot_loop_start(current_time,**kwargs)
class LowCrowdMonth(PositioningPortfolio):allocation_mode='crowd'
class LowPositionMonth(PositioningPortfolio):allocation_mode='position'
class ConstantPositioningControl(PositioningPortfolio):allocation_mode='constant'
