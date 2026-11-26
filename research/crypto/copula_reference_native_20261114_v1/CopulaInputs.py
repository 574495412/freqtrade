"""Actual native input admission only; financial startup is rejected directly."""
import hashlib,importlib.util,json
from pathlib import Path
import pandas as pd
from freqtrade.enums import RunMode
from freqtrade.strategy import IStrategy


def owner(path):
    spec=importlib.util.spec_from_file_location('local_copula_reference',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


class CopulaInputs(IStrategy):
    INTERFACE_VERSION=3
    timeframe='1h'
    startup_candle_count=504
    can_short=True
    minimal_roi={}
    stoploss=-.99

    def ft_bot_start(self,**kwargs):
        raise ValueError('Input-only copula admission; financial lifecycle, quantities and funding unfinished')

    def bot_start(self,**kwargs):
        if not self.config.get('research_diagnostic_only')or self.config.get('runmode')!=RunMode.BACKTEST:raise ValueError('Diagnostic-only native input study')
        self.module=owner(Path(self.config['research_input_owner']))
        admission=json.loads(Path(self.config['research_admission']).read_text())
        if not admission['passed_for_hourly_prices']:raise ValueError('Input admission failed')
        expected=[s+'/USDT:USDT'for s in self.module.SYMBOLS]
        if self.config['exchange']['pair_whitelist']!=expected:raise ValueError('Source20universe changed')
        receipts=json.loads(Path(admission['data_receipts']['path']).read_text());columns={}
        for symbol,pair in zip(self.module.SYMBOLS,expected,strict=True):
            name=f'futures/{symbol}_USDT_USDT-1h-futures.feather';path=Path(self.config['datadir'])/name
            if hashlib.sha256(path.read_bytes()).hexdigest()!=receipts[name]:raise ValueError('Source hourly price drift')
            frame=self.dp.get_pair_dataframe(pair,'1h','futures')
            columns[symbol]=frame.set_index('date').close
        self.research_prices=pd.DataFrame(columns)
        self.terminal=pd.Timestamp(admission['terminal_EOS'])

    def populate_indicators(self,dataframe,metadata):
        symbol=metadata['pair'].split('/')[0]
        dataframe['source_tradable']=self.module.tradable_mask(dataframe.date,symbol,self.terminal if symbol=='EOS'else None)
        return dataframe

    def populate_entry_trend(self,dataframe,metadata):
        dataframe['enter_long']=0;dataframe['enter_short']=0;return dataframe

    def populate_exit_trend(self,dataframe,metadata):
        dataframe['exit_long']=0;dataframe['exit_short']=0;return dataframe

    def confirm_trade_entry(self,*args,**kwargs):return False
