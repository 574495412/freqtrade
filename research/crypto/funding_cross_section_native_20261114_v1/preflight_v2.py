"""Actual native StrategyResolver/DataProvider admission plus source-rule reconciliation."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
ENGINE=Path('/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')
sys.path.insert(0,str(ENGINE));sys.path.insert(0,str(HERE))
from FundingFeatures import observed_daily_funding,source_inputs
from freqtrade.data.dataprovider import DataProvider
from freqtrade.enums import CandleType,RunMode
from freqtrade.resolvers import StrategyResolver


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n')


def main():
    admission=json.loads((HERE/'input_admission.json').read_text());out=Path(admission['funding_events']['path']).parent
    manifest=ENGINE.parent/'FREQTRADE_SOURCE.json';source=json.loads(manifest.read_text())
    for name,digest in source['files'].items():
        if sha(ENGINE/name)!=digest:raise ValueError('Native engine drift')
    receipts=json.loads(Path(admission['data_receipts']['path']).read_text())
    for name,digest in receipts.items():
        if sha(out/'candles'/name)!=digest:raise ValueError('Candle drift')
    old=ROOT/'research/crypto/spread_liquidity_native_20261114_v1/EdgeLiquidity_1000.json'
    config=json.loads(old.read_text());config.update(strategy='FundingSignal',strategy_path=str(HERE),
        user_data_dir=out,datadir=out/'candles',runmode=RunMode.BACKTEST,candle_type_def=CandleType.FUTURES,
        timeframe='1h',startup_candle_count=336,timerange='1735689600-1788220799',
        research_admission=str(HERE/'input_admission.json'),research_diagnostic_only=True)
    config['exchange']['pair_whitelist']=admission['active_pairs']
    owner=StrategyResolver.load_strategy(config);owner.dp=DataProvider(config,None);owner.bot_start()
    inputs=owner.research_inputs;daily=owner.research_daily
    events=pd.read_parquet(admission['funding_events']['path'])
    invalid=[(r['symbol']+'/USDT:USDT',pd.Timestamp(r.get('event_time',r.get('date'))).floor('D')) for r in admission['issues']]
    owners=[HERE/'FundingFeatures.py',HERE/'FundingSignal.py',Path(__file__)]
    save(HERE/'implementation_freeze_v2.json',{'native_engine_commit':source['commit'],
        'native_manifest_sha256':sha(manifest),'native_account_returns_seen':False,
        'native_sources':{str(p):sha(p) for p in owners},
        'input_admission_sha256':sha(HERE/'input_admission.json')})
    prefix=[]
    for text in ['2025-07-01T00:00Z','2026-01-01T00:00Z','2026-08-01T00:00Z']:
        cutoff=pd.Timestamp(text);last=cutoff-pd.Timedelta(days=1)
        past=events.event_time<cutoff
        short=observed_daily_funding(events.loc[past],admission['active_pairs'],daily.index[0],last,invalid)
        changed=events.copy();changed.loc[~past,'last_funding_rate']+=.01
        altered=observed_daily_funding(changed,admission['active_pairs'],daily.index[0],daily.index[-1],invalid)
        # Include the decision at cutoff: it depends only on completed earlier events.
        short=short.reindex(pd.date_range(daily.index[0],cutoff,freq='D'))
        for key in ['funding7','factor','weights']:
            expected=inputs[key].loc[:cutoff]
            pd.testing.assert_frame_equal(expected,source_inputs(short)[key].loc[:cutoff],check_exact=True)
            pd.testing.assert_frame_equal(expected,source_inputs(altered)[key].loc[:cutoff],check_exact=True)
        prefix.append({'cutoff':text,'actual_native_prefix_and_future_perturbation':True})
    # The exact pinned source is used only on artificial price probes to expose
    # its W matrix. These are algebra tests, never account/performance evidence.
    path=HERE/'source/alpha_research/factors/carry.py'
    spec=importlib.util.spec_from_file_location('source_carry_formula',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    calendar=pd.date_range(pd.Timestamp('2023-01-01',tz='UTC'),daily.index[-1],freq='D')
    funding=daily.reindex(calendar);zero=pd.DataFrame(0.,index=calendar,columns=daily.columns)
    baseline=module.cross_sectional_factor(funding,zero,cost_oneway_bps=0)
    weight_error=0.
    for pair in daily.columns:
        probe=zero.copy();probe[pair]=1.
        exposed=module.cross_sectional_factor(funding,probe,cost_oneway_bps=0)-baseline
        error=np.max(np.abs(exposed.loc[daily.index].to_numpy()-inputs['weights'][pair].to_numpy()))
        weight_error=max(weight_error,float(error))
    if weight_error>1e-12:raise ValueError('Pinned source quantile/anchor/tie mismatch')
    records=[]
    for pair in admission['active_pairs']:
        hours=owner.dp.get_pair_dataframe(pair,'1h','futures')
        actual=owner.advise_indicators(hours.copy(),{'pair':pair})
        selected=actual.loc[actual.date.between(pd.Timestamp('2025-01-01T00:00Z'),pd.Timestamp('2026-08-31T23:00Z'))]
        expected=pd.date_range('2025-01-01T00:00Z','2026-08-31T23:00Z',freq='h')
        if not pd.DatetimeIndex(selected.date).as_unit('ns').equals(expected.as_unit('ns')):raise ValueError('Native hourly gap')
        selected[['date','funding7','funding_factor','source_target_weight']].to_parquet(out/(pair.split('/')[0]+'_native_signal_view.parquet'),index=False)
        records.append({'pair':pair,'actual_native_hourly_rows':len(selected),'unknown_hourly_factor_rows':int(selected.funding_factor.isna().sum())})
    for flag in [False,True]:
        try:
            if not flag:
                owner.config['research_diagnostic_only']=False;owner.bot_start()
            else:owner.bot_loop_start()
        except ValueError:pass
        else:raise ValueError('Unfinished strategy allowed a financial run')
    inputs['factor'].to_parquet(out/'native_factor.parquet');inputs['weights'].to_parquet(out/'native_weights.parquet');daily.to_parquet(out/'native_daily_funding.parquet')
    gaps=json.loads((HERE/'tia_rest_probe.json').read_text())['response']['structuredContent']['result']
    missing=pd.Timestamp('2026-06-24T04:00Z')
    if any(pd.Timestamp(r['fundingTime'],unit='ms',tz='UTC').floor('h')==missing for r in gaps):raise ValueError('Gap resolved; input plan must be reviewed')
    raw=events.loc[events.pair.eq('TIA/USDT:USDT')].set_index('event_time')
    for row in gaps:
        stamp=pd.Timestamp(row['fundingTime'],unit='ms',tz='UTC')
        if abs(raw.loc[stamp,'last_funding_rate']-float(row['fundingRate']))>1e-12:raise ValueError('REST/archive conflict')
    affected=inputs['factor']['TIA/USDT:USDT'].loc['2025-01-01':].loc[lambda x:x.isna()]
    ties=[r for r in inputs['decisions'] if r['overlap_members'] and r['date']>='2025-01-01']
    save(HERE/'native_preflight.json',{'passed_for_input_diagnostics':True,'financial_admission':False,'native_accounts':0,
        'actual_native_strategy_loader':str(sys.modules[StrategyResolver.__module__].__file__),
        'source_weight_cells_checked':len(daily)*len(daily.columns),'maximum_source_weight_error':weight_error,
        'source_artificial_probe_is_account_result':False,'prefix_checks':prefix,'records':records,
        'source_overlap_decisions':ties,'missing_funding_event':missing.isoformat(),
        'rest_archive_observations_matched':len(gaps),'missing_event_zero_payment_certified':False,
        'affected_tia_factor_days':affected.index.strftime('%Y-%m-%d').tolist(),
        'source_tia_target_weight_on_gap_day':float(inputs['weights'].loc[missing.floor('D'),'TIA/USDT:USDT']),
        'source_target_is_actual_native_position':False,'independent_oos_certified':False})
    print('NATIVE INPUT VIEW',len(records),'pairs;',sum(r['actual_native_hourly_rows'] for r in records),'rows; source overlap decisions',len(ties),'; funding gap remains UNKNOWN; 0 accounts')


if __name__=='__main__':main()
