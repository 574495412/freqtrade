"""Bounded actual IStrategy rolling-model construction, not funded backtesting."""
import json,sys,threading,time,os,hashlib
from pathlib import Path
from .nominate import HERE,ROOT,OLD,OUT

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
    assert not(OUT/'model_capsule.json').exists(),'Retain completed model capsule'
    for d in ['models']:(OUT/d).mkdir(parents=True,exist_ok=True)
    plan=json.loads((ROOT/'research/crypto/pairs_cointegration_native_20261114_v1/plan.json').read_text());sys.path.insert(0,plan['engine_source']);sys.path.insert(0,str(ROOT/'research/crypto/pairs_cointegration_native_20261114_v1/dependencies'));sys.path.insert(0,str(OLD/'dependencies'))
    import pandas as pd
    import psutil
    from freqtrade.resolvers import StrategyResolver
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.enums import RunMode,CandleType
    cfg=json.loads(Path(plan['candidates'][0]['config']['path']).read_text());a=json.loads((OLD/'input_admission.json').read_text());cfg.update(strategy='FormationInputs',strategy_path=str(HERE),user_data_dir=OUT,datadir=Path(a['data_receipts']['path']).parent/'candles',research_root=str(ROOT),research_diagnostic_only=True,research_fit_models=True,research_output=str(OUT),runmode=RunMode.BACKTEST,candle_type_def=CandleType.FUTURES)
    cfg['exchange']['pair_whitelist']=[s+'/USDT:USDT'for s in a['source_universe']]
    identity={str(p):sha(p)for p in [HERE/'formation_protocol.json',HERE/'FormationInputs.py',ROOT/'freqtrade/data/copula_formation.py',ROOT/'freqtrade/data/copula_reference.py',OLD/'artifact_manifest.json',Path(a['data_receipts']['path'])]}
    (OUT/'model_identity_before_fit.json').write_text(json.dumps(identity,indent=2)+'\n')
    finished=threading.Event();started=time.monotonic()
    def monitor():
        p=psutil.Process()
        while not finished.wait(5):
            r={'pid':p.pid,'elapsed':time.monotonic()-started,'rss':p.memory_info().rss};(OUT/'fit_resources.json').write_text(json.dumps(r)+'\n')
            if r['elapsed']>3600 or r['rss']>3*1024**3:os._exit(73)
    threading.Thread(target=monitor,daemon=True).start()
    try:
        owner=StrategyResolver.load_strategy(cfg);owner.dp=DataProvider(cfg,None);owner.bot_start()
        forecasts=pd.concat(owner.forecasts).sort_index();assert len(forecasts)==14592 and not forecasts.index.has_duplicates
        assert forecasts.index.as_unit('ns').equals(pd.date_range('2025-01-01T00:00Z','2026-08-31T23:00Z',freq='h').as_unit('ns'))
        forecasts.to_parquet(OUT/'native_hourly_forecasts.parquet')
        assert all(sha(p)==v for p,v in identity.items())
        eos={str(alpha):int((forecasts.symbol1.eq('EOS')|forecasts.symbol2.eq('EOS')).mul(forecasts[f'state_{alpha}'].ne(0)).sum())for alpha in [10,15,20]}
        capsule={'identity':identity,'weekly_records':len(owner.fitted),'fitted_weeks':sum(r['status']=='FITTED'for r in owner.fitted),'no_trading_weeks':sum(r['status']!='FITTED'for r in owner.fitted),'forecasts':{'path':str(OUT/'native_hourly_forecasts.parquet'),'sha256':sha(OUT/'native_hourly_forecasts.parquet')},'all_model_files':[{'path':str(p),'sha256':sha(p)}for p in sorted((OUT/'models').glob('*.json'))],'EOS_nonzero_hourly_decisions':eos,'native_accounts':0,'financial_returns':'UNKNOWN'}
        (OUT/'model_capsule.json').write_text(json.dumps(capsule,indent=2)+'\n');print('CAPSULE',capsule['weekly_records'],capsule['fitted_weeks'],eos,flush=True)
    finally:finished.set()
if __name__=='__main__':main()
