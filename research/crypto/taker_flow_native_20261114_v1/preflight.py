"""Native feature input, actual-prefix and independent scalar validation."""
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
ENGINE=Path('/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')
sys.path.insert(0,str(ENGINE));sys.path.insert(0,str(HERE))
from FlowFeatures import FIELDS,calculate_features
from freqtrade.data.dataprovider import DataProvider
from freqtrade.enums import RunMode,CandleType
from freqtrade.resolvers import StrategyResolver


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n')


def main():
    admission=json.loads((HERE/'input_admission_v2.json').read_text());protocol=json.loads((HERE/'protocol.json').read_text())
    if not admission['passed']:raise ValueError('Canonical execution/flow input not admitted')
    out=Path(admission['flow_input']['path']).parent
    manifest=json.loads((ENGINE.parent/'FREQTRADE_SOURCE.json').read_text())
    for name,digest in manifest['files'].items():
        if sha(ENGINE/name)!=digest:raise ValueError('Native engine drift')
    for name,digest in json.loads(Path(admission['data_receipts']['path']).read_text()).items():
        if sha(Path(admission['native_datadir'])/name)!=digest:raise ValueError('Native price input drift')
    config=json.loads((ROOT/'research/crypto/spread_liquidity_native_20261114_v1/EdgeLiquidity_1000.json').read_text())
    for key in list(config):
        if key.startswith('research_'):config.pop(key)
    config.update(strategy='FlowSignal',strategy_path=str(HERE),runmode=RunMode.BACKTEST,
        candle_type_def=CandleType.FUTURES,timeframe='1h',startup_candle_count=336,
        user_data_dir=out,datadir=Path(admission['native_datadir']),timerange='1735689600-1788220799',
        research_admission=str(HERE/'input_admission_v2.json'),research_diagnostic_only=True)
    config['exchange']['pair_whitelist']=protocol['pairs']
    sources=[HERE/name for name in ['FlowFeatures.py','FlowSignal.py','flow_author_kernel.py','preflight.py']]
    save(HERE/'implementation_freeze.json',{'before_feature_or_return_outputs':True,'native_engine_commit':manifest['commit'],
        'protocol_sha256':sha(HERE/'protocol.json'),'data_contract_v2_sha256':sha(HERE/'data_contract_v2.json'),
        'sources':{str(p):sha(p) for p in sources},'native_funded_accounts':0})
    owner=StrategyResolver.load_strategy(config);owner.dp=DataProvider(config,None);owner.bot_start()
    flow=pd.read_parquet(admission['flow_input']['path'])
    days=pd.date_range('2025-01-01T00:00Z','2026-08-31T16:00Z',freq='4h')
    records=[];combined=[];mapping_rows=0;scalar_cells=0
    for pair in protocol['pairs']:
        hours=owner.dp.get_pair_dataframe(pair,'1h','futures')
        raw=flow.loc[flow.pair.eq(pair)].reset_index(drop=True)
        features=owner.research_features[pair]
        for text in ['2025-07-01T00:00Z','2026-01-01T00:00Z','2026-08-01T00:00Z']:
            cutoff=pd.Timestamp(text);past=hours.date<cutoff
            prefix=calculate_features(hours.loc[past],raw.loc[raw.date<cutoff])
            changed=raw.copy();changed.loc[changed.date>=cutoff,'taker_buy_base_volume']*=.1
            changed.loc[changed.date>=cutoff,'number_of_trades']*=3
            altered=calculate_features(hours,changed)
            expected=features.loc[features.decision_date<=cutoff].reset_index(drop=True)
            pd.testing.assert_frame_equal(expected,prefix.reset_index(drop=True),check_exact=True)
            pd.testing.assert_frame_equal(expected,altered.loc[altered.decision_date<=cutoff].reset_index(drop=True),check_exact=True)
        actual=owner.advise_indicators(hours.copy(),{'pair':pair})
        actual=actual.loc[actual.date.between(pd.Timestamp('2025-01-01T00:00Z'),pd.Timestamp('2026-08-31T23:00Z'))]
        expected_hours=pd.date_range('2025-01-01T00:00Z','2026-08-31T23:00Z',freq='h')
        if not pd.DatetimeIndex(actual.date).as_unit('ns').equals(expected_hours.as_unit('ns')):raise ValueError('Native calendar not full')
        reference=features.set_index('decision_date').reindex(pd.DatetimeIndex(actual.date))[FIELDS]
        if not np.array_equal(reference.to_numpy(),actual[FIELDS].to_numpy(),equal_nan=True):raise ValueError('Native field timing map mismatch')
        actual[['date',*FIELDS]].to_parquet(out/(pair.split('/')[0]+'_native_indicators.parquet'),index=False)
        values=raw.set_index('date');expected=features.set_index('decision_date')
        errors={field:[] for field in FIELDS}
        for day in days:
            window=values.loc[day-pd.Timedelta(hours=20):day-pd.Timedelta(hours=1)]
            if len(window)!=20:raise ValueError('Scalar source window incomplete')
            volume=window.base_volume.tolist();buy=window.taker_buy_base_volume.tolist();trades=window.number_of_trades.tolist()
            delta=[2*b-v for b,v in zip(buy,volume,strict=True)]
            sizes=[v/t if t>0 else float('nan') for v,t in zip(volume,trades,strict=True)]
            deviation=statistics.stdev(sizes) if all(math.isfinite(s) for s in sizes) else float('nan')
            candidate={'cvd_20':math.fsum(delta)/math.fsum(volume) if math.fsum(volume)>0 else float('nan'),
                'taker_imbalance_ma_20':math.fsum(d/v for d,v in zip(delta,volume,strict=True))/20 if min(volume)>0 else float('nan'),
                'avg_trade_size_z_20':(sizes[-1]-math.fsum(sizes)/20)/deviation if deviation>0 else float('nan')}
            for field,scalar in candidate.items():
                native_value=float(expected.at[day,field])
                if math.isnan(scalar) and math.isnan(native_value):
                    errors[field].append(0.);continue
                error=abs(scalar-native_value)
                if not math.isfinite(error) or error>1e-8:raise ValueError(f'Independent scalar feature mismatch {pair} {day} {field}: {error}')
                errors[field].append(error)
        records.append({'pair':pair,'native_hourly_rows':len(actual),'scalar_decision_dates':len(days),
            'scalar_maximum_errors':{field:max(error) for field,error in errors.items()},'prefix_boundaries':3,
            'unknown_feature_cells':int(features.set_index('decision_date').reindex(days)[FIELDS].isna().sum().sum())})
        combined.append(features.assign(pair=pair));mapping_rows+=len(actual);scalar_cells+=len(days)*3
    pd.concat(combined,ignore_index=True).to_parquet(out/'native_features.parquet',index=False)
    for diagnostic in [False,True]:
        try:
            if not diagnostic:owner.config['research_diagnostic_only']=False;owner.bot_start()
            else:owner.bot_loop_start()
        except ValueError:pass
        else:raise ValueError('Unfinished financial lifecycle allowed')
    save(HERE/'native_preflight.json',{'passed':True,'native_strategy_source':str(sys.modules[StrategyResolver.__module__].__file__),
        'native_strategy_class':'FlowSignal','actual_native_indicator_rows':mapping_rows,
        'independent_scalar_feature_cells':scalar_cells,'records':records,
        'native_funded_accounts':0,'funding_admission':False,'financial_returns':None})
    print('NATIVE INPUT COMPLETE',mapping_rows,'hourly rows;',scalar_cells,'scalar cells; 0 funded accounts')


if __name__=='__main__':main()
