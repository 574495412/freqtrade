"""Local native IStrategy admission/feature view; funded execution is unfinished."""
import hashlib
import json
from pathlib import Path

import pandas as pd
from FundingFeatures import observed_daily_funding,source_inputs
from freqtrade.strategy import IStrategy


class FundingSignal(IStrategy):
    INTERFACE_VERSION=3
    timeframe='1h'
    startup_candle_count=336
    can_short=True
    minimal_roi={}
    stoploss=-.99
    order_types={'entry':'market','exit':'market','stoploss':'market','stoploss_on_exchange':False}

    def bot_start(self,**kwargs):
        admission=json.loads(Path(self.config['research_admission']).read_text())
        record=admission['funding_events'];path=Path(record['path'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:raise ValueError('Funding input drift')
        events=pd.read_parquet(path)
        invalid=[(row['symbol']+'/USDT:USDT',pd.Timestamp(row.get('event_time',row.get('date'))).floor('D')) for row in admission['issues']]
        daily=observed_daily_funding(events,admission['active_pairs'],'2024-12-01T00:00Z','2026-08-31T00:00Z',invalid)
        self.research_inputs=source_inputs(daily)
        self.research_daily=daily
        # A diagnostic-only flag is not financial authorization or a completed native account.
        if not self.config.get('research_diagnostic_only',False):
            raise ValueError('Funding input/settlement and weight-resizing execution are not admitted; no funded account')

    def populate_indicators(self,dataframe,metadata):
        day=pd.DatetimeIndex(dataframe.date.dt.floor('D'));pair=metadata['pair']
        dataframe['funding7']=self.research_inputs['funding7'][pair].reindex(day).to_numpy()
        dataframe['funding_factor']=-dataframe.funding7
        dataframe['source_target_weight']=self.research_inputs['weights'][pair].reindex(day).to_numpy()
        return dataframe

    def populate_entry_trend(self,dataframe,metadata):
        # This class is an input view. It cannot silently generate an all-cash funded benchmark.
        dataframe['enter_long']=0;dataframe['enter_short']=0
        return dataframe

    def populate_exit_trend(self,dataframe,metadata):
        dataframe['exit_long']=0;dataframe['exit_short']=0
        return dataframe

    def confirm_trade_entry(self,*args,**kwargs):return False

    def bot_loop_start(self,*args,**kwargs):
        raise ValueError('Input-only native view: funded account lifecycle is not implemented')
