"""Local crypto transfer of source v5 expanding signal; native finances only."""
from pathlib import Path

import numpy as np
import pandas as pd
from CausalGeoHMM import native_signals
from NativeQuantityPortfolio import NativeQuantityPortfolio

from freqtrade.data.history import load_pair_history
from freqtrade.enums import CandleType,RunMode


class GeoHmmPortfolio(NativeQuantityPortfolio):
    can_short=False
    allocation_mode=None

    def bot_start(self,**kwargs):
        if self.config.get('runmode')!=RunMode.BACKTEST or not self.config.get('dry_run'):
            raise ValueError('Isolated native historical research only')
        pairs=self.config['exchange']['pair_whitelist']
        if pairs!=['BTC/USDT:USDT']:raise ValueError('Frozen singleBTC crypto transfer')
        pair=pairs[0];directory=Path(self.config['datadir']);study=Path(self.config['research_study'])
        output=Path(self.config['research_model_output'])
        raw=load_pair_history(pair,'1d',directory,fill_up_missing=False,drop_incomplete=False,candle_type=CandleType.FUTURES)
        close=raw.set_index('date').close
        signals,models=native_signals(close,study,output)
        days=pd.date_range('2025-01-01','2026-08-31',tz='UTC',freq='D')
        selected=signals.reindex(days)
        if selected.isna().any().any():raise ValueError('Full608day signal contract missing')
        self.research_weights=pd.DataFrame({pair:selected['target_'+self.allocation_mode]},index=days)
        self.research_features={pair:pd.DataFrame({'decision_date':days,
            'target_weight':self.research_weights[pair].to_numpy(),'source_signal':selected.source_signal.to_numpy(),
            'source_volatility':selected.source_volatility.to_numpy()})}
        self.research_allocation_diagnostics={'models':models,'mode':self.allocation_mode,'source_equity_spot_replication':False,
             'native_actual_quantity_rebalance':True,'source_price_prefix':'native dailyBTC2021Oct--2026Aug',
             'source_calendar252day_refit_preserved':True,'source_strategy_financial_code_executed':False}
        self._prices={pair:self.dp.get_pair_dataframe(pair,'1h','futures').set_index('date').open}
        self._marks={pair:self.dp.get_pair_dataframe(pair,'1h','mark').set_index('date').open}
        self._targets={d.strftime('%Y-%m-%d'):{pair:int(np.sign(self.research_weights.at[d,pair]))}for d in days}
        self.research_trace={'attempts':[],'accepted':[],'adjustments':[],'budgets':[]}
        self._context=None;self._attempted=set()

    def bot_loop_start(self,current_time,**kwargs):
        self.position_adjustment_enable=pd.Timestamp(current_time).hour==1
        return super().bot_loop_start(current_time,**kwargs)

class SourceGeoHmmBTC(GeoHmmPortfolio):allocation_mode='source'
class GeoOnlyBTC(GeoHmmPortfolio):allocation_mode='geometric'
class HmmOnlyBTC(GeoHmmPortfolio):allocation_mode='markov'
class ConstantGrossBTC(GeoHmmPortfolio):allocation_mode='always'
