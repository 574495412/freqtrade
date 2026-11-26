"""Actual native IStrategy feature view; no unfinished all-cash account allowed."""
import hashlib
import json
from pathlib import Path

import pandas as pd
from FlowFeatures import FIELDS,calculate_features
from freqtrade.strategy import IStrategy


class FlowSignal(IStrategy):
    INTERFACE_VERSION=3
    timeframe='1h'
    startup_candle_count=336
    can_short=True
    minimal_roi={}
    stoploss=-.99
    order_types={'entry':'market','exit':'market','stoploss':'market','stoploss_on_exchange':False}

    def bot_start(self,**kwargs):
        admission=json.loads(Path(self.config['research_admission']).read_text())
        if not admission['passed']:raise ValueError('Raw flow/execution input admission rejected')
        record=admission['flow_input'];path=Path(record['path'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:raise ValueError('Flow input drift')
        raw=pd.read_parquet(path);self.research_features={}
        for pair in self.config['exchange']['pair_whitelist']:
            hours=self.dp.get_pair_dataframe(pair,'1h','futures')
            self.research_features[pair]=calculate_features(hours,raw.loc[raw.pair.eq(pair)])
        if not self.config.get('research_diagnostic_only',False):
            raise ValueError('Feature input view only; no funded execution admission')

    def populate_indicators(self,dataframe,metadata):
        values=self.research_features[metadata['pair']].set_index('decision_date')
        for field in FIELDS:dataframe[field]=values[field].reindex(pd.DatetimeIndex(dataframe.date)).to_numpy()
        return dataframe

    def populate_entry_trend(self,dataframe,metadata):
        dataframe['enter_long']=0;dataframe['enter_short']=0
        return dataframe

    def populate_exit_trend(self,dataframe,metadata):
        dataframe['exit_long']=0;dataframe['exit_short']=0
        return dataframe

    def confirm_trade_entry(self,*args,**kwargs):return False

    def bot_loop_start(self,*args,**kwargs):
        raise ValueError('Input-only strategy: funded lifecycle is not implemented')
