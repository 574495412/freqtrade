"""Native diagnostic custody of PCA/OU features; all funded entry points closed."""
import hashlib
import json
from pathlib import Path

import pandas as pd
from freqtrade.data.pca_ou import pca_ou_diagnostics
from freqtrade.enums import RunMode
from freqtrade.strategy import IStrategy


class PcaInputs(IStrategy):
    INTERFACE_VERSION=3
    timeframe='1h'
    startup_candle_count=0
    can_short=True
    minimal_roi={}
    stoploss=-.99

    def bot_start(self,**kwargs):
        if (self.config.get('research_diagnostic_only')is not True
            or self.config.get('dry_run')is not True or self.config.get('runmode')!=RunMode.BACKTEST):
            raise ValueError('PCA input diagnostic only')
        path=Path(self.config['research_admission'])
        assert hashlib.sha256(path.read_bytes()).hexdigest()==self.config['research_admission_sha256']
        admission=json.loads(path.read_text())
        assert self.config['exchange']['pair_whitelist']==admission['pairs']
        data={}
        for r in admission['native_inputs']:
            assert hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()==r['sha256']
            frame=self.dp.get_pair_dataframe(r['pair'],'1h','futures').set_index('date')
            data[r['pair']]=frame.close
        self.research_close=pd.DataFrame(data)
        self.research_components=pca_ou_diagnostics(self.research_close,
            pd.date_range(admission['decision_start'],admission['decision_end'],freq='h'))

    def ft_bot_start(self,**kwargs):
        raise ValueError('Diagnostic cannot start funded account')

    def populate_indicators(self,dataframe,metadata):
        for name,frame in self.research_components.items():
            dataframe[name]=frame[metadata['pair']].reindex(pd.DatetimeIndex(dataframe.date)).to_numpy()
        return dataframe

    def populate_entry_trend(self,dataframe,metadata):
        dataframe['enter_long']=0;dataframe['enter_short']=0
        return dataframe

    def populate_exit_trend(self,dataframe,metadata):
        dataframe['exit_long']=0;dataframe['exit_short']=0
        return dataframe

    def confirm_trade_entry(self,*args,**kwargs):return False

    def bot_loop_start(self,*args,**kwargs):
        raise ValueError('Diagnostic cash curve is not an account')
