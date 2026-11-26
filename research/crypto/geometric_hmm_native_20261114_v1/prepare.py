"""Native strategy preflight and actual source-model causality/audit checks."""
import copy
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
OUT=ROOT/'user_data/crypto_quant/geometric_hmm_native_20261114_v1'
ENGINE=Path('/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def ref(p):return {'path':str(Path(p).resolve()),'sha256':sha(p)}
def save(p,x):Path(p).write_text(json.dumps(x,indent=2,default=str)+'\n')

def independent_filter(saved):
    from scipy.special import logsumexp
    x=saved['X_test'];means=saved['means'];variance=saved['variances'];d=x.shape[1]
    emit=-.5*(d*np.log(2*np.pi)+np.log(variance).sum(axis=1)[None,:]+((x[:,None,:]-means[None,:,:])**2/variance[None,:,:]).sum(axis=2))
    with np.errstate(divide='ignore'):start=np.log(saved['startprob']);transition=np.log(saved['transmat'])
    rows=[];alpha=None
    for i in range(len(x)):
        prediction=start if alpha is None else logsumexp(alpha[:,None]+transition,axis=0)
        alpha=prediction+emit[i];alpha-=logsumexp(alpha);rows.append(np.exp(alpha))
    return np.asarray(rows)

def main():
    assert not(HERE/'preflight.json').exists()
    manifest=json.loads((ENGINE.parent/'FREQTRADE_SOURCE.json').read_text())
    for p,h in manifest['files'].items():assert sha(ENGINE/p)==h,p
    sys.path.insert(0,str(ENGINE));sys.path.insert(0,str(HERE))
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.data.history import load_pair_history
    from freqtrade.enums import CandleType,RunMode
    from freqtrade.resolvers import ExchangeResolver,StrategyResolver
    from CausalGeoHMM import calculate,source_modules
    source_modules(HERE)  # Activate private statistical dependencies before threadpoolctl.
    from threadpoolctl import threadpool_limits
    old=json.loads((HERE.parent/'tsmom_risk_native_20261114_v1/v002/plan.json').read_text())
    base=json.loads(Path(old['candidates'][0]['config']['path']).read_text())
    base.update(strategy='SourceGeoHmmBTC',position_adjustment_enable=True,max_entry_position_adjustment=-1,
                max_open_trades=1,timeframe='1h',startup_candle_count=72,timeframe_detail='5m',margin_mode='cross',
                research_study=str(HERE),research_model_output=str(OUT),dry_run_wallet=1000)
    base['exchange']['pair_whitelist']=['BTC/USDT:USDT']
    save(HERE/'SourceGeoHmmBTC_1000.json',base)
    cfg=copy.deepcopy(base);cfg.update(strategy_path=str(HERE),user_data_dir=OUT,datadir=OUT/'candles',
                     runmode=RunMode.BACKTEST,candle_type_def=CandleType.FUTURES,timerange='1735689600-1788220799')
    snapshot=json.loads(Path(old['market_snapshot']['path']).read_text())
    exchange=ExchangeResolver.load_exchange(cfg,validate=False,load_leverage_tiers=False)
    try:
        exchange._markets=snapshot['markets'];exchange._leverage_tiers=snapshot['leverage_tiers']
        exchange._api.set_markets(snapshot['markets'],snapshot['currencies']);exchange._api_async.set_markets(snapshot['markets'],snapshot['currencies'])
        owner=StrategyResolver.load_strategy(cfg);owner.dp=DataProvider(cfg,exchange);owner.bot_start()
        print('Actual native model fitting complete',flush=True)
        capsule=json.loads((OUT/'model_capsule.json').read_text());features=pd.read_parquet(OUT/'native_model_features.parquet')
        independent_cells=0;max_error=0.
        for m in capsule['models']:
            if m['fallback']:raise ValueError('Source-defined fallback observed; preserve beforefinancialadmission')
            saved=np.load(m['model_path']);expected=independent_filter(saved)
            error=float(np.max(np.abs(expected-saved['probabilities'])));max_error=max(max_error,error)
            independent_cells+=expected.size
        assert max_error<1e-10,max_error
        close=load_pair_history('BTC/USDT:USDT','1d',OUT/'candles',fill_up_missing=False,drop_incomplete=False,candle_type=CandleType.FUTURES).set_index('date').close
        causal=[]
        for text in ['2025-07-01T00:00Z','2026-01-01T00:00Z','2026-08-01T00:00Z']:
            cutoff=pd.Timestamp(text);past=close.index<cutoff
            truncated,models=calculate(close.loc[past],HERE,OUT/'technical'/('truncated_'+cutoff.strftime('%Y%m%d')))
            changed=close.copy();changed.loc[~past]*=1.123
            altered,_=calculate(changed,HERE,OUT/'technical'/('perturbed_'+cutoff.strftime('%Y%m%d')))
            prior=features.loc[features.index<cutoff]
            pd.testing.assert_frame_equal(prior,truncated,check_exact=True,check_freq=False)
            pd.testing.assert_frame_equal(prior,altered.loc[altered.index<cutoff],check_exact=True,check_freq=False)
            causal.append({'cutoff':str(cutoff),'actual_prefix_refit_models':len(models),'truncation_and_perturbation':True})
            print('Actual refit prefix+perturbation passed',text,flush=True)
        # Source public fit_and_filter, unchanged, independently repeats an
        # actual last fold; no source financial backtest is executed.
        geo,markov,ensemble=source_modules(HERE);returns=np.log(close/close.shift(1)).dropna()
        last=capsule['models'][-1];train=returns.loc[:pd.Timestamp(last['train_end'])]
        test=returns.loc[pd.Timestamp(last['test_start']):pd.Timestamp(last['test_end'])]
        with threadpool_limits(limits=1):mom,crisis=markov.fit_and_filter_markov(train,test)
        np.testing.assert_allclose(mom,features.loc[test.index,'momentum_probability'],rtol=0,atol=1e-12)
        np.testing.assert_allclose(crisis,features.loc[test.index,'crisis_probability'],rtol=0,atol=1e-12)
        checks=[]
        for strategy in ['SourceGeoHmmBTC','GeoOnlyBTC','HmmOnlyBTC','ConstantGrossBTC']:
            config=copy.deepcopy(cfg);config['strategy']=strategy
            actual=StrategyResolver.load_strategy(config);actual.dp=DataProvider(config,exchange);actual.bot_start()
            pair='BTC/USDT:USDT';raw=actual.dp.get_pair_dataframe(pair,'1h','futures')
            mapped=actual.populate_indicators(raw.copy(),{'pair':pair})
            for field in ['target_weight','source_signal','source_volatility']:
                expected=actual.research_features[pair].set_index('decision_date')[field].reindex(pd.DatetimeIndex(raw.date.dt.floor('D'))).to_numpy()
                np.testing.assert_array_equal(mapped[field],expected)
            assert len(actual.research_weights)==608 and actual.research_weights.iloc[0,0]in[0.,.5,1.]
            actual.research_weights.to_parquet(OUT/f'preflight_weights_{strategy}.parquet')
            actual.research_features[pair].to_parquet(OUT/f'preflight_features_{strategy}.parquet')
            checks.append({'strategy':strategy,'exact_hourly_indicator_cells':len(raw)*3,'days':608})
        save(HERE/'preflight.json',{'passed':True,'native_original_files_verified':len(manifest['files']),
             'native_commit':manifest['commit'],'native_strategies':checks,'source_models':capsule['models'],
             'independent_forward_probability_cells':independent_cells,'maximum_forward_probability_error':max_error,
             'actual_causal_refit_checks':causal,'actual_source_last_fold_repeat':True,'source_account_engine_executed':False,
             'native_funded_accounts':0,'partial_source_summary_guard_not_used_for_streaming_features':True})
    finally:exchange.close()

if __name__=='__main__':main()
