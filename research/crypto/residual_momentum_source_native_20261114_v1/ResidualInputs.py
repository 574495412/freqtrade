"""Actual native IStrategy/DataProvider feature owner, input-only admission."""
import hashlib
import json
from pathlib import Path

import pandas as pd

from freqtrade.data.residual_momentum import source_residual_momentum
from freqtrade.enums import RunMode
from freqtrade.strategy import IStrategy


class ResidualInputs(IStrategy):
    INTERFACE_VERSION=3
    timeframe='1h'
    startup_candle_count=1464
    can_short=True
    minimal_roi={}
    stoploss=-.99

    def bot_start(self,**kwargs):
        if not self.config.get('research_diagnostic_only') or self.config.get('runmode')!=RunMode.BACKTEST:
            raise ValueError('Input admission only; no funded strategy or service')
        admission=json.loads(Path(self.config['research_admission']).read_text())
        if self.config['exchange']['pair_whitelist']!=admission['pairs']:
            raise ValueError('Source eight-asset universe changed')
        data={}
        for row in admission['native_inputs']:
            path=Path(row['path'])
            if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:
                raise ValueError('Native feature input drift')
            data[row['pair']]=self.dp.get_pair_dataframe(row['pair'],'1h','futures').set_index('date').close
        self.research_close=pd.DataFrame(data)
        self.research_components=source_residual_momentum(self.research_close)

    def populate_indicators(self,dataframe,metadata):
        previous=pd.DatetimeIndex(dataframe.date)-pd.Timedelta(hours=1)
        for key in ['signal','raw']:
            dataframe['residual_'+key]=self.research_components[key][metadata['pair']].reindex(previous).to_numpy()
        return dataframe

    def populate_entry_trend(self,dataframe,metadata):
        dataframe['enter_long']=0;dataframe['enter_short']=0
        return dataframe

    def populate_exit_trend(self,dataframe,metadata):
        dataframe['exit_long']=0;dataframe['exit_short']=0
        return dataframe

    def confirm_trade_entry(self,*args,**kwargs):return False

    def bot_loop_start(self,*args,**kwargs):
        raise ValueError('Input-only strategy must not produce an all-cash financial benchmark')
