"""Preserve original candles; derive exact15mraw bars and native-load preflight."""
import hashlib,json,os,sys,importlib.util
from pathlib import Path
import numpy as np
import pandas as pd
from .bootstrap import boot,HERE,ROOT
OUT=ROOT/'user_data/crypto_quant/volume_profile_native_20261114_v1'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def ref(p):return {'path':str(Path(p).resolve()),'sha256':sha(p)}
def save(p,v):Path(p).write_text(json.dumps(v,indent=2,default=str)+'\n')
def main():
    boot();p=json.loads((HERE/'protocol.json').read_text());assert sha(HERE/'protocol.json')==json.loads((HERE/'protocol_freeze.json').read_text())['sha256']
    OUT.mkdir(exist_ok=True,parents=True);directory=OUT/'candles';(directory/'futures').mkdir(exist_ok=True,parents=True)
    parent=ROOT/'research/crypto/freqai_rf_native_20261114_v1/v002/plan.json';plan=json.loads(parent.read_text());base=Path(plan['datadir']);expected=json.loads(Path(plan['data_receipts']['path']).read_text());receipts={}
    for relative,digest in expected.items():
        assert sha(base/relative)==digest
        target=directory/relative
        if not target.exists():os.link(base/relative,target)
        assert sha(target)==digest;receipts[relative]=digest
    raw=pd.read_feather(directory/'futures/BTC_USDT_USDT-5m-futures.feather');start=pd.Timestamp('2024-12-30T00:00Z');end=pd.Timestamp('2026-09-01T00:00Z')
    selected=raw.loc[raw.date.ge(start)&raw.date.lt(end)].copy();grid=pd.date_range(start,end-pd.Timedelta(minutes=5),freq='5min')
    assert pd.DatetimeIndex(selected.date).as_unit('ns').equals(grid.as_unit('ns'))
    assert np.isfinite(selected[['open','high','low','close','volume']]).all().all()
    bars=selected.set_index('date').resample('15min').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).reset_index()
    assert len(bars)*3==len(selected)
    from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
    from freqtrade.data.history import load_pair_history
    from freqtrade.enums import CandleType,RunMode
    FeatherDataHandler(directory).ohlcv_store('BTC/USDT:USDT','15m',bars,CandleType.FUTURES)
    loaded=load_pair_history('BTC/USDT:USDT','15m',directory,fill_up_missing=False,drop_incomplete=False,candle_type=CandleType.FUTURES)
    pd.testing.assert_frame_equal(bars,loaded,check_exact=True,check_dtype=False)
    file=directory/'futures/BTC_USDT_USDT-15m-futures.feather';receipts[str(file.relative_to(directory))]=sha(file);save(OUT/'data_receipts.json',receipts)
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.resolvers import StrategyResolver
    from ProfileRules import features
    fields=features(loaded)
    checks=[]
    for name in p['policies']:
        cfg=json.loads((ROOT/'research/crypto/dual_thrust_native_20261114_v1/SourceDualBTC_1000.json').read_text());cfg.update(strategy=name,strategy_path=str(HERE),timeframe='15m',startup_candle_count=192,runmode=RunMode.BACKTEST,datadir=directory,dataformat_ohlcv='feather',user_data_dir=OUT)
        strategy=StrategyResolver.load_strategy(cfg);strategy.dp=DataProvider(cfg,None);strategy.bot_start()
        calculated=strategy.advise_indicators(loaded.copy(),{'pair':'BTC/USDT:USDT'})
        for col in ['poc','vah','val','volume_mean']:np.testing.assert_array_equal(calculated[col],fields[col])
        checks.append({'strategy':name,'native_strategy_resolved':True,'native_indicator_rows':len(calculated)})
    # Source pure profile only, never its financial backtester.
    source=ROOT/'research/crypto/mechanism_source_intake_20261114_v1/sources/profile/indicadores.py';spec=importlib.util.spec_from_file_location('profile_source_reference',source);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    original=mod.VolumeProfile.adicionar_value_area_diaria(loaded.set_index('date'),100,70,20)
    for col in ['poc','vah','val']:np.testing.assert_array_equal(fields[col],original[col])
    np.testing.assert_array_equal(fields.volume_mean,original.volume_media)
    prefix_checks=0
    for cutoff in ['2025-02-13T17:30Z','2025-09-09T10:15Z','2026-06-04T04:45Z']:
        cut=pd.Timestamp(cutoff);perturbed=loaded.copy();future=perturbed.date.gt(cut)
        perturbed.loc[future,['open','high','low','close']]*=1.7;perturbed.loc[future,'volume']*=3
        changed=features(perturbed);truncated=features(loaded.loc[loaded.date.le(cut)])
        columns=['date','poc','vah','val','volume_mean'];a=fields.loc[fields.date.le(cut),columns].reset_index(drop=True)
        pd.testing.assert_frame_equal(a,changed.loc[changed.date.le(cut),columns].reset_index(drop=True),check_exact=True)
        pd.testing.assert_frame_equal(a,truncated[columns].reset_index(drop=True),check_exact=True);prefix_checks+=2
    fields.to_parquet(OUT/'preflight_features.parquet',index=False)
    save(HERE/'preflight.json',{'passed':True,'checks':checks,'source_profile_rows_exact':len(fields),'source_pure_histogram_and_volume_mean_exact':True,'future_truncation_checks':prefix_checks,'financial_accounts':0,'new_15m_bars':len(loaded),'original_5m_bars':len(selected),'all_old_candles_preserved':True,'native_data_receipts':ref(OUT/'data_receipts.json'),'features':ref(OUT/'preflight_features.parquet')})
    print('Native preflight passed',checks,'prefixchecks',prefix_checks,flush=True)
if __name__=='__main__':main()
