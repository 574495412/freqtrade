"""Native IStrategy integration, sharing tested actual quantity/cash callbacks."""
from pathlib import Path

import numpy as np
import pandas as pd
from ClockedPortfolio import ClockedPortfolio,ClockedGrossOneRiskTS
from PairWeights import SYMBOLS,pair_weights

from freqtrade.data.history import load_pair_history
from freqtrade.enums import CandleType


class PairPortfolio(ClockedPortfolio):
    selection_mode=None

    def bot_start(self,**kwargs):
        pairs=self.config['exchange']['pair_whitelist']
        if pairs!=[s+'/USDT:USDT' for s in SYMBOLS]:raise ValueError('Source ten-symbol universe changed')
        directory=Path(self.config['datadir']);daily={};self._prices={};self._marks={}
        for pair in pairs:
            symbol=pair.split('/')[0]
            daily[symbol]=load_pair_history(symbol+'/USDT','1d',directory,fill_up_missing=False,
                    drop_incomplete=False,candle_type=CandleType.SPOT).set_index('date').close
            self._prices[pair]=self.dp.get_pair_dataframe(pair,'1h','futures').set_index('date').open
            self._marks[pair]=self.dp.get_pair_dataframe(pair,'1h','mark').set_index('date').open
        close=pd.DataFrame(daily)
        days=pd.date_range('2025-01-01',self.config.get('research_decision_end','2026-08-31'),freq='D',tz='UTC')
        weights,diagnostics,panel=pair_weights(close,days,self.selection_mode)
        weights.columns=pairs
        self.research_weights=weights;self.research_allocation_diagnostics=diagnostics
        self.research_pair_states=panel;self.research_features={}
        for pair in pairs:
            self.research_features[pair]=pd.DataFrame(dict(decision_date=days,
                target_weight=weights[pair].to_numpy(),source_signal=weights[pair].to_numpy(),
                source_volatility=np.nan))
        self._targets={d.strftime('%Y-%m-%d'):{p:int(np.sign(weights.at[d,p])) for p in pairs} for d in days}
        self.research_trace={'attempts':[],'accepted':[],'adjustments':[],'budgets':[]}
        self._context=None;self._attempted=set()


class SourceNaivePairs(PairPortfolio):selection_mode='naive'
class AdjustedEGPairs(PairPortfolio):selection_mode='adjusted'
class TrendGrossOne(ClockedGrossOneRiskTS):pass
