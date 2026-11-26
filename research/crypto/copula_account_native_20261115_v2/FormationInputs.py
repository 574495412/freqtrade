"""Full20native admission plus fixed rolling formation, financial start guarded."""
import hashlib,importlib.util,json
from pathlib import Path
import pandas as pd
from freqtrade.enums import RunMode
from freqtrade.strategy import IStrategy


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


class FormationInputs(IStrategy):
    INTERFACE_VERSION=3
    timeframe='1h';startup_candle_count=504;can_short=True;minimal_roi={};stoploss=-.99
    def ft_bot_start(self,**kwargs):raise ValueError('Formation-only; native funded execution not yet admitted')
    def bot_start(self,**kwargs):
        if not self.config.get('research_diagnostic_only')or self.config.get('runmode')!=RunMode.BACKTEST:raise ValueError('Native diagnostic inputs only')
        root=Path(self.config['research_root']);old=root/'research/crypto/copula_reference_native_20261114_v1'
        a=json.loads((old/'input_admission.json').read_text());receipts=json.loads(Path(a['data_receipts']['path']).read_text());reference=module('copula_reference_v1',root/'freqtrade/data/copula_reference.py');formation=module('copula_formation_v2',root/'freqtrade/data/copula_formation.py')
        if self.config['exchange']['pair_whitelist']!=[s+'/USDT:USDT'for s in reference.SYMBOLS]:raise ValueError('Full source20required')
        columns={}
        for symbol in reference.SYMBOLS:
            name=f'futures/{symbol}_USDT_USDT-1h-futures.feather';p=Path(self.config['datadir'])/name
            if hashlib.sha256(p.read_bytes()).hexdigest()!=receipts[name]:raise ValueError('Native source input drift')
            f=self.dp.get_pair_dataframe(symbol+'/USDT:USDT','1h','futures');columns[symbol]=f.set_index('date').close
        self.prices=pd.DataFrame(columns);self.models=[];terminal=pd.Timestamp(a['terminal_EOS'])
        self.fitted=[];self.forecasts=[]
        if self.config.get('research_fit_models'):
            import pyvinecopulib as pv
            destination=Path(self.config['research_output']);(destination/'models').mkdir(exist_ok=True,parents=True)
        for stamp in pd.date_range('2024-12-27','2026-08-28',freq='7D',tz='UTC'):
            nomination=formation.nominate(self.prices,stamp,terminal,reference);self.models.append(nomination)
            if self.config.get('research_fit_models'):
                record,model=formation.fit_week(self.prices,nomination,pv,reference)
                (destination/'models'/f'{stamp.strftime("%Y%m%d")}.json').write_text(json.dumps(record,indent=2)+'\n')
                self.fitted.append(record)
                self.forecasts.append(formation.forecast_week(self.prices,record,model,pv,reference,'2025-01-01T00:00Z','2026-09-01T00:00Z'))
                (destination/'model_progress.json').write_text(json.dumps({'closed_weeks':len(self.fitted),'last_week':stamp.isoformat(),'last_status':record['status'],'native_accounts':0},indent=2)+'\n')
                print('NATIVE_MODEL',stamp.isoformat(),record['status'],flush=True)
    def populate_indicators(self,dataframe,metadata):return dataframe
    def populate_entry_trend(self,dataframe,metadata):dataframe['enter_long']=0;dataframe['enter_short']=0;return dataframe
    def populate_exit_trend(self,dataframe,metadata):dataframe['exit_long']=0;dataframe['exit_short']=0;return dataframe
