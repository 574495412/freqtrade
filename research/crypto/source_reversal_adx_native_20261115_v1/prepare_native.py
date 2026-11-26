"""Native source preflight, fixed data grids and actual-account registration."""
import copy
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
OUT=ROOT/'user_data/crypto_quant/source_reversal_adx_native_20261115_v1'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def ref(path):return dict(path=str(Path(path).resolve()),sha256=sha(path))
def save(path,obj):Path(path).write_text(json.dumps(obj,indent=2,default=str,allow_nan=False)+'\n')


def main():
    assert not (HERE/'plan.json').exists(),'No replacement of a registered account matrix'
    protocol=json.loads((HERE/'protocol.json').read_text())
    assert sha(HERE/'protocol.json')==json.loads((HERE/'protocol_freeze.json').read_text())['sha256']
    OUT.mkdir(exist_ok=False);data=OUT/'candles';(data/'futures').mkdir(parents=True)
    original=ROOT/'user_data/crypto_quant/nfi40_data_v002/candles/futures'
    bindings={};checks=[]
    for pair in protocol['pairs']:
        stem=pair.replace('/','_').replace(':','_')
        for tf,kind,freq in [('1h','futures','1h'),('5m','futures','5min'),('1h','mark','1h'),('1h','funding_rate','8h')]:
            name=f'{stem}-{tf}-{kind}.feather';source=original/name;path=data/'futures'/name
            os.link(source,path);bindings['futures/'+name]=sha(path)
            frame=pd.read_feather(path);dates=pd.DatetimeIndex(frame.date)
            assert dates.is_unique and dates.is_monotonic_increasing
            current=frame.loc[frame.date.ge(pd.Timestamp(protocol['start']))&frame.date.lt(pd.Timestamp(protocol['end']))]
            expected=pd.date_range(protocol['start'],pd.Timestamp(protocol['end'])-pd.Timedelta(freq),freq=freq)
            assert pd.DatetimeIndex(current.date).as_unit('ns').equals(expected.as_unit('ns')),(pair,kind)
            assert np.isfinite(current.select_dtypes('number').to_numpy()).all()
            if kind!='funding_rate':assert (current[['open','high','low','close']].to_numpy()>0).all()
            checks.append(dict(pair=pair,type=kind,timeframe=tf,rows=len(current),expected=len(expected),source=str(source),sha256=sha(path)))
    save(OUT/'data_receipts.json',bindings)
    save(HERE/'full_input_grid_audit.json',dict(passed=True,records=checks,
        supplied_funding_events=sum(r['rows']for r in checks if r['type']=='funding_rate'),
        frequency='Explicit8hour source8contract, not all coins universal cadence',private_settlement_certified=False))
    parent=HERE.parent/'dual_thrust_native_20261114_v1/plan.json'
    plan=json.loads(parent.read_text())
    plan.update(schema='freqtrade.native.source-reversal-adx.v1',registered_at=datetime.now(UTC).isoformat(),
        output_root=str(OUT),datadir=str(data),data_receipts=ref(OUT/'data_receipts.json'),
        pairs=protocol['pairs'],candidates=[],allowed_leverage=1.,total_cost_bps_per_side=[15,8],
        runner_module='research.crypto.source_reversal_adx_native_20261115_v1.native_runner',
        runner_sha256=sha(HERE/'native_runner.py'),native_start=protocol['start'],
        additional_source_seals=[ref(HERE/'protocol.json'),ref(HERE/'CaptureRules.py'),
            *[ref(HERE/'sources'/n)for n in ['Strategy002.py','ADXMomentum.py','README.md','LICENSE']]])
    plan['marks']={p:ref(data/'futures'/(p.replace('/','_').replace(':','_')+'-1h-mark.feather'))for p in protocol['pairs']}
    for name in protocol['main_strategies']+[protocol['benchmark']]:
        for capital in ([1000]if name=='HoldEight'else[1000,500]):
            config=json.loads((HERE.parent/'pairs_cointegration_native_20261114_v1/TrendGrossOne_1000.json').read_text())
            for k in list(config):
                if k.startswith('research_')or k in ['minimal_roi','stoploss','order_types','use_exit_signal','exit_profit_only','ignore_roi_if_entry_signal','trailing_stop','trailing_stop_positive','trailing_stop_positive_offset']:
                    config.pop(k)
            config.update(strategy=name,dry_run_wallet=capital,max_open_trades=8,
                position_adjustment_enable=False,max_entry_position_adjustment=0,margin_mode='isolated',
                startup_candle_count=1000,timeframe='1h'if name=='SourceADX'else'5m',
                timeframe_detail='5m'if name=='SourceADX'else'',research_source_pair=True)
            config['exchange']['pair_whitelist']=protocol['pairs']
            path=HERE/f'{name}_{capital}.json';save(path,config)
            files={n:ref(HERE/'sources'/n)for n in ['Strategy002.py','ADXMomentum.py','LICENSE']}
            files['SourcePortfolio.py']=ref(HERE/'SourcePortfolio.py')
            plan['candidates'].append(dict(name=f'{name}_{capital}',strategy=name,capital=capital,
                pairs=protocol['pairs'],config=ref(path),source_files=files))
    save(HERE/'plan.json',plan)
    engine=Path(plan['engine_source']);manifest=json.loads(Path(plan['engine_manifest']['path']).read_text())
    for path,digest in manifest['files'].items():assert sha(engine/path)==digest
    sys.path[:0]=[str(engine),str(HERE),str(HERE/'sources')]
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.enums import RunMode
    from freqtrade.resolvers import StrategyResolver
    from CaptureRules import signal_receipt
    from Strategy002 import Strategy002
    from ADXMomentum import ADXMomentum
    receipts={};parity=prefixes=availability=0;loaded=[]
    for candidate in plan['candidates']:
        if candidate['capital']!=1000:continue
        cfg=json.loads(Path(candidate['config']['path']).read_text())
        cfg.update(runmode=RunMode.BACKTEST,datadir=data,user_data_dir=ROOT/'user_data',
            strategy_path=str(HERE),timerange=f"{int(pd.Timestamp(plan['start']).timestamp())}-{int(pd.Timestamp(plan['end']).timestamp())-1}")
        owner=StrategyResolver.load_strategy(cfg);owner.dp=DataProvider(cfg,None)
        assert owner.startup_candle_count==1000 and owner.timeframe==cfg['timeframe']
        expected_roi={0:.01}if candidate['strategy']=='SourceADX'else({}if candidate['strategy']=='HoldEight'else{0:.05,20:.04,30:.03,60:.01})
        assert owner.minimal_roi==expected_roi
        assert owner.stoploss==(-.25 if candidate['strategy']=='SourceADX'else(-.99 if candidate['strategy']=='HoldEight'else-.10))
        loaded.append(dict(strategy=candidate['strategy'],timeframe=owner.timeframe,startup=owner.startup_candle_count,
            roi=owner.minimal_roi,stoploss=owner.stoploss,order_types=owner.order_types,exit_profit_only=owner.exit_profit_only))
        for pair in protocol['pairs']:
            raw=owner.dp.get_pair_dataframe(pair,owner.timeframe,'futures')
            actual=owner.ft_advise_signals(owner.advise_indicators(raw.copy(),{'pair':pair}),{'pair':pair})
            receipts[candidate['strategy']+'|'+pair]=signal_receipt(actual,candidate['strategy'])
            if candidate['strategy']in ['SourceReversal','SourceADX']:
                original_owner=(Strategy002 if candidate['strategy']=='SourceReversal'else ADXMomentum)(copy.deepcopy(cfg))
                expected=original_owner.populate_indicators(raw.copy(),{'pair':pair})
                expected=original_owner.populate_entry_trend(expected,{'pair':pair})
                expected=original_owner.populate_exit_trend(expected,{'pair':pair})
                assert signal_receipt(actual,candidate['strategy'])==signal_receipt(expected,candidate['strategy'])
                parity+=len(actual)
            if candidate['strategy']=='ReversalInADXTrend':
                known=pd.to_datetime(actual.date_1h,utc=True)+pd.Timedelta(hours=1)
                assert not(known.dropna()>actual.date.loc[known.notna()]+pd.Timedelta(minutes=5)).any()
                availability+=int(known.notna().sum())
            # Full native callbacks must preserve the pre-cutoff history under
            # both truncation and future-price perturbation, with equal start.
            for cutoff in pd.to_datetime(['2025-07-01','2026-01-01','2026-08-01'],utc=True):
                previous=raw.date<cutoff;changed=raw.copy()
                changed.loc[~previous,['open','high','low','close']]*=11
                if owner.timeframe=='5m':end_decision=cutoff-pd.Timedelta(minutes=5)
                else:end_decision=cutoff-pd.Timedelta(hours=1)
                for altered in [raw.loc[previous].copy(),changed]:
                    test=StrategyResolver.load_strategy(copy.deepcopy(cfg))
                    if candidate['strategy']=='ReversalInADXTrend':
                        higher=owner.dp.get_pair_dataframe(pair,'1h','futures').copy()
                        higher.loc[higher.date.ge(cutoff),['open','high','low','close']]*=11
                        class Provider:
                            def get_pair_dataframe(self,*args,**kwargs):return higher.copy()
                        test.dp=Provider()
                    else:test.dp=owner.dp
                    observed=test.ft_advise_signals(test.advise_indicators(altered,{'pair':pair}),{'pair':pair})
                    cols=signal_receipt(actual,candidate['strategy'])['fields']
                    pd.testing.assert_frame_equal(actual.loc[actual.date.le(end_decision),['date',*cols]].reset_index(drop=True),
                        observed.loc[observed.date.le(end_decision),['date',*cols]].reset_index(drop=True),check_exact=True)
                    prefixes+=1
    save(HERE/'preflight_signal_receipts.json',receipts)
    save(HERE/'preflight.json',dict(passed=True,native_files=len(manifest['files']),plan=ref(HERE/'plan.json'),
        source_exact_indicator_signal_rows=parity,actual_prefix_callbacks=prefixes,
        informative_availability_cells=availability,loaded=loaded,financial_accounts_started=False,
        actual_native_source_classes=True,new_factor_analysis_calls=0,bench_records=0))
    print('PREFLIGHT',parity,'source rows;',prefixes,'native prefix callbacks;0financial accounts started')


if __name__=='__main__':main()
