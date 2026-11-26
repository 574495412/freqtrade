"""Actual native strategy/data path and independent input/rank/expiry audit."""
import copy,hashlib,json,sys
from pathlib import Path
import numpy as np
import pandas as pd
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2];OUT=ROOT/'user_data/crypto_quant/positioning_month_native_20261114_v1'
ENGINE=Path('/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def ref(p):return {'path':str(Path(p).resolve()),'sha256':sha(p)}
def save(p,x):Path(p).write_text(json.dumps(x,indent=2,default=str)+'\n')
def main():
    assert not(HERE/'preflight.json').exists()
    records=json.loads((OUT/'raw_receipts.json').read_text());assert len(records)==2922
    for row in records:
        assert sha(row['path'])==row['sha256']
        check=Path(row.get('checksum_path',str(Path(row['path']).with_suffix('.CHECKSUM'))))
        assert sha(check)==row['checksum_sha256']
        assert check.read_text().split()[0]==row['sha256']
    manifest=json.loads((ENGINE.parent/'FREQTRADE_SOURCE.json').read_text())
    for p,d in manifest['files'].items():assert sha(ENGINE/p)==d,p
    sys.path.insert(0,str(ENGINE));sys.path.insert(0,str(HERE))
    from freqtrade.enums import CandleType,RunMode
    from freqtrade.resolvers import ExchangeResolver,StrategyResolver
    from freqtrade.data.dataprovider import DataProvider
    from Signals import owner,signal_frames,opportunities
    old=json.loads((HERE.parent/'geometric_hmm_native_20261114_v1/plan.json').read_text())
    base=json.loads(Path(old['candidates'][0]['config']['path']).read_text())
    base.update(strategy='LowCrowdMonth',max_open_trades=3,research_study=str(HERE),research_model_output=str(OUT),dry_run_wallet=1000)
    pairs=['BTC/USDT:USDT','ETH/USDT:USDT','SOL/USDT:USDT'];base['exchange']['pair_whitelist']=pairs
    save(HERE/'LowCrowdMonth_1000.json',base)
    cfg=copy.deepcopy(base);cfg.update(strategy_path=str(HERE),user_data_dir=OUT,datadir=OUT/'candles',runmode=RunMode.BACKTEST,
        candle_type_def=CandleType.FUTURES,timerange='1735689600-1788220799')
    snapshot=json.loads(Path(old['market_snapshot']['path']).read_text())
    ex=ExchangeResolver.load_exchange(cfg,validate=False,load_leverage_tiers=False)
    try:
        ex._markets=snapshot['markets'];ex._leverage_tiers=snapshot['leverage_tiers']
        ex._api.set_markets(snapshot['markets'],snapshot['currencies']);ex._api_async.set_markets(snapshot['markets'],snapshot['currencies'])
        actual=StrategyResolver.load_strategy(cfg);actual.dp=DataProvider(cfg,ex);actual.bot_start()
        frames,capsule=signal_frames(HERE,OUT);module=owner(HERE)
        scalar_cells=0;unknown={};prefix=[]
        for symbol,daily in frames.items():
            assert len(daily)==974
            for column in ['count_long_short_ratio','sum_toptrader_long_short_ratio']:
                rank=module.trailing_midrank(daily[column]);values=daily[column].tolist()
                for i in range(365,len(values)):
                    history=values[i-365:i];current=values[i]
                    expected=(sum(v<current for v in history)+.5*sum(v==current for v in history))/365 if np.isfinite([current,*history]).all()else np.nan
                    assert np.isclose(rank.iloc[i],expected,rtol=0,atol=1e-15,equal_nan=True)
                    scalar_cells+=1
                for cutoff in ['2025-07-01','2026-01-01','2026-07-01']:
                    mask=daily.index<pd.Timestamp(cutoff,tz='UTC');changed=daily[column].copy();changed.loc[~mask]*=1.777
                    pd.testing.assert_series_equal(rank.loc[mask],module.trailing_midrank(changed).loc[mask],check_exact=True)
                    pd.testing.assert_series_equal(rank.loc[mask],module.trailing_midrank(daily.loc[mask,column]),check_exact=True)
                    mode='crowd'if column=='count_long_short_ratio'else'position'
                    original=opportunities(HERE,daily,mode)
                    modified=daily.copy();modified.loc[~mask,column]*=1.777
                    other=opportunities(HERE,modified,mode)
                    # Decision D uses yesterday23:55; all D<=cutoff must agree.
                    stop=original.index<=pd.Timestamp(cutoff,tz='UTC')
                    pd.testing.assert_frame_equal(original.loc[stop],other.loc[stop],check_exact=True)
                    prefix.append({'symbol':symbol,'column':column,'cutoff':cutoff,'actual_prefix_and_future_perturbation':True})
            unknown[symbol]=daily.isna().sum().to_dict()
        rows=[];opportunity_checks=0
        for strategy in ['LowCrowdMonth','LowPositionMonth','ConstantPositioningControl']:
            config=copy.deepcopy(cfg);config['strategy']=strategy
            a=StrategyResolver.load_strategy(config);a.dp=DataProvider(config,ex);a.bot_start()
            assert a.research_weights.shape==(608,3)and a.research_weights.ge(0).all().all()
            assert a.research_weights.sum(axis=1).le(1+1e-15).all()
            for pair in pairs:
                op=a.research_opportunities[pair];events=op.new_opportunity.tolist()
                for i,value in enumerate(op.active):
                    expected=any(events[j]for j in range(len(events))if j<=i<j+30)
                    assert value==expected;opportunity_checks+=1
                op.to_parquet(OUT/f'preflight_opportunities_{strategy}_{pair.split("/")[0]}.parquet')
                raw=a.dp.get_pair_dataframe(pair,'1h','futures');mapped=a.populate_indicators(raw.copy(),{'pair':pair})
                for field in ['target_weight','source_signal','source_volatility']:
                    expected=a.research_features[pair].set_index('decision_date')[field].reindex(pd.DatetimeIndex(raw.date.dt.floor('D'))).to_numpy()
                    np.testing.assert_array_equal(expected,mapped[field])
                a.research_features[pair].to_parquet(OUT/f'preflight_features_{strategy}_{pair.split("/")[0]}.parquet')
            a.research_weights.to_parquet(OUT/f'preflight_weights_{strategy}.parquet')
            rows.append({'strategy':strategy,'decision_weight_cells':a.research_weights.size,
                         'unknown_rank_decisions':{p:int((~d.known).sum())for p,d in a.research_opportunities.items()},
                         'new_opportunities':{p:int(d.new_opportunity.sum())for p,d in a.research_opportunities.items()},
                         'active_coin_days':int(a.research_weights.gt(0).sum().sum())})
        save(HERE/'preflight.json',{'passed':True,'source_original_native_files':len(manifest['files']),
             'official_archives':len(records),'daily_rows':2922,'raw_rows':sum(x['raw_rows']for rows in capsule['diagnostics'].values()for x in rows),
             'unknown_daily_fields':unknown,'independent_rank_cells':scalar_cells,'independent_expiry_cells':opportunity_checks,
             'actual_prefix_checks':prefix,'native_strategies':rows,'native_accounts':0,'historical_transmission_certified':False})
        print('Native strategy and actual archive/rank/expiry checks passed',flush=True)
    finally:ex.close()
if __name__=='__main__':main()
