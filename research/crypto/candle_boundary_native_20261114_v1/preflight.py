"""Actual StrategyResolver/DataProvider interface and causal clock admission."""
import json,sys
from pathlib import Path
import pandas as pd
from .prepare import HERE,ROOT,OUT,sha,save

def main():
    p=json.loads((ROOT/'research/crypto/session_reversal_native_20261113_v1/plan.json').read_text())
    manifest=json.loads(Path(p['engine_manifest']['path']).read_text())
    assert all(sha(Path(p['engine_source'])/n)==h for n,h in manifest['files'].items())
    assert {str(x.relative_to(p['engine_source']))for x in Path(p['engine_source']).rglob('*.py')}=={n for n in manifest['files']if n.endswith('.py')}
    sys.path.insert(0,p['engine_source'])
    from freqtrade.resolvers import StrategyResolver
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.enums import RunMode,CandleType
    prices=pd.read_feather(OUT/'candles/futures/BTC_USDT_USDT-1m-futures.feather')
    records=[]
    for strategy,phase in [('BoundaryStartMinute',0),('BoundaryEndMinute',14),('ConstantMinuteControl',None)]:
        config=json.loads(Path(p['candidates'][0]['config']['path']).read_text())
        config.update(strategy=strategy,strategy_path=str(HERE),timeframe='1m',runmode=RunMode.BACKTEST,dry_run_wallet=1000,user_data_dir=OUT,candle_type_def=CandleType.FUTURES)
        owner=StrategyResolver.load_strategy(config);owner.dp=DataProvider(config,None)
        def calc(f):return owner.advise_exit(owner.advise_entry(owner.advise_indicators(f.copy(),{'pair':'BTC/USDT:USDT'}),{'pair':'BTC/USDT:USDT'}),{'pair':'BTC/USDT:USDT'})
        actual=calc(prices)
        expected=actual.date.add(pd.Timedelta(minutes=1)).dt.minute.mod(15).eq(phase)if phase is not None else pd.Series(True,index=prices.index)
        assert actual.enter_long.eq(expected.astype(int)).all()
        assert actual.exit_long.eq(0).all()
        owner.dp._set_cached_df('BTC/USDT:USDT','1m',actual,CandleType.FUTURES)
        owner.dp._set_dataframe_max_index('BTC/USDT:USDT',50000)
        sliced=owner.dp.get_analyzed_dataframe('BTC/USDT:USDT','1m')[0]
        assert len(sliced)==1000 and sliced.date.iloc[-1]==actual.date.iloc[49999]
        checks=[]
        for edge in ['2025-07-01T00:00Z','2026-01-01T00:00Z','2026-08-01T00:00Z']:
            mask=prices.date.le(pd.Timestamp(edge));prefix=calc(prices.loc[mask]);changed=prices.copy();changed.loc[~mask,['open','high','low','close']]*=3
            perturbed=calc(changed)
            fields=['scheduled_entry','enter_long','exit_long']
            pd.testing.assert_frame_equal(actual.loc[mask,fields],prefix[fields]);pd.testing.assert_frame_equal(actual.loc[mask,fields],perturbed.loc[mask,fields])
            checks.append({'cutoff':edge,'prefix_exact':True,'future_perturbation_exact':True})
        # Boundary expected clocks are tested directly, before financial results.
        day=actual.loc[actual.date.between('2025-01-01','2025-01-01 23:59')]
        if phase is not None:assert day.enter_long.sum()==96
        actual[['date','scheduled_entry','enter_long','exit_long']].to_parquet(OUT/f'preflight_{strategy}.parquet',index=False)
        records.append({'strategy':strategy,'rows':len(actual),'phase':phase,'prefix_checks':checks,'native_dataprovider':True})
    save(HERE/'preflight.json',{'passed':True,'engine_commit':p['engine_commit'],'unmodified_engine_files':len(manifest['files']),'actual_native_strategy_classes':3,'records':records,'all_source_minute_labels_resolved':False,'financial_accounts':0})
    print('Admitted3native1mstrategies,9prefix+perturbationchecks')
if __name__=='__main__':main()
