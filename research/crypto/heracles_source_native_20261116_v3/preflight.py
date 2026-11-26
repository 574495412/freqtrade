"""Actual native strategy initialization and independent past-only signal audit."""

import importlib.util
import json
import math
import shutil
import socket
import sys
from pathlib import Path

import numpy as np
import pandas as pd


HERE=Path(__file__).resolve().parent


def independent(frame):
    """Scalar rolling source math,not ta functions or candidate calculator."""
    high,low,close=(frame[c].to_numpy() for c in ['high','low','close'])
    kcw=np.full(len(frame),np.nan)
    dcp=kcw.copy()
    for i in range(len(frame)):
        if i>=19:
            h,l,c=high[i-19:i+1],low[i-19:i+1],close[i-19:i+1]
            middle=math.fsum((h+l+c)/3)/20
            upper=math.fsum((4*h-2*l+c)/3)/20
            lower=math.fsum((-2*h+4*l+c)/3)/20
            kcw[i]=(upper-lower)/middle*100
        if i>=9:
            top=max(high[i-9:i+1]);bottom=min(low[i-9:i+1])
            dcp[i]=(close[i]-bottom)/(top-bottom) if top>bottom else np.nan
    left=pd.Series(dcp).shift(15).to_numpy()
    right=pd.Series(kcw).shift(9).to_numpy()
    ratio=left/right
    return kcw,dcp,(np.isfinite(ratio)&(ratio>=.16)&(ratio<=.75)).astype(int)


def main():
    plan=json.loads((HERE/'plan.json').read_text())
    sys.path.insert(0,str(HERE/'dependencies'))
    sys.path.insert(0,plan['engine_source'])
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize.backtesting import HEADERS, Backtesting
    from freqtrade.resolvers import ExchangeResolver
    candidate=plan['candidates'][0]
    out=Path(plan['output_root'])/'preflight_v2'
    out.mkdir(exist_ok=False)
    strategies=out/'strategies';strategies.mkdir()
    shutil.copyfile(HERE/'sources/Heracles.py',strategies/'Heracles.py')
    shutil.copyfile(HERE/'HeraclesNative.py',strategies/'HeraclesNative.py')
    raw=json.loads(Path(candidate['config']['path']).read_text())
    raw.update(datadir=plan['datadir'],user_data_dir=str(out),strategy_path=str(strategies),
               db_url='sqlite://',export='none',
               timerange=f"{int(pd.Timestamp(plan['native_start']).timestamp())}-{int(pd.Timestamp(plan['end']).timestamp())-1}")
    (out/'config.json').write_text(json.dumps(raw,indent=2)+'\n')
    config=setup_optimize_configuration({'config':[str(out/'config.json')],'datadir':plan['datadir']},RunMode.BACKTEST)
    exchange=ExchangeResolver.load_exchange(config,validate=False,load_leverage_tiers=False)
    market=json.loads(Path(candidate['market_snapshot']['path']).read_text())
    exchange._markets,exchange._leverage_tiers=market['markets'],market['leverage_tiers']
    exchange._api.set_markets(market['markets'],market['currencies'])
    exchange._api_async.set_markets(market['markets'],market['currencies'])
    exchange.validate_config(config)
    def offline(*args,**kwargs):
        raise RuntimeError('Native source preflight is offline')
    socket.socket.connect=offline
    spec=importlib.util.spec_from_file_location('fixed_ticks',plan['price_precision_source']['path'])
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    class Probe(module.MatchedPricePrecisionMixin,Backtesting):
        pass
    account=None
    try:
        account=Probe(config,exchange=exchange)
        account.setup_frozen_price_precision(pairs=tuple(candidate['pairs']))
        strategy=account.strategylist[0]
        account._set_strategy(strategy)
        actual_params={key:getattr(strategy,key).value for key in strategy.buy_params}
        assert actual_params==strategy.buy_params
        assert actual_params=={'buy_indicator_shift':15,'buy_crossed_indicator_shift':9,
                               'buy_div_min':.16,'buy_div_max':.75}
        assert strategy.stoploss==-.256 and strategy.timeframe=='4h'
        assert strategy.minimal_roi=={0:.598,644:.166,3269:.115,7289:0}
        assert account.required_startup==100
        data,_=account.load_bt_data()
        shapes=[]
        for pair,frame in data.items():
            result=strategy.populate_indicators(frame.copy(),{'pair':pair})
            shapes.append({'pair':pair,'before_rows':len(frame),'after_rows':len(result),
                           'columns':list(frame),'nulls':frame.isna().sum().to_dict(),
                           'zeros':frame.eq(0).sum().to_dict(),'dtypes':{k:str(v) for k,v in frame.dtypes.items()}})
            frame.to_parquet(out/(pair.split('/')[0]+'_raw_native.parquet'),index=False)
        (HERE/'native_shape_diagnostic.json').write_text(json.dumps(shapes,default=int,indent=2)+'\n')
        processed=strategy.advise_all_indicators(data)
        indicators={pair:f.copy() for pair,f in processed.items()}
        converted=account._get_ohlcv_as_lists(processed)
        grid=pd.date_range(plan['start'],'2026-08-31T20:00Z',freq='4h').as_unit('ns')
        checks=[];prefix_cells=0
        for pair,values in converted.items():
            source=data[pair]
            kcw,dcp,flag=independent(source)
            assert len(indicators[pair])==len(source)
            np.testing.assert_allclose(indicators[pair].volatility_kcw,kcw,atol=1e-9,rtol=1e-10,equal_nan=True)
            np.testing.assert_allclose(indicators[pair].volatility_dcp,dcp,atol=1e-12,rtol=1e-12,equal_nan=True)
            rows=pd.DataFrame(values,columns=HEADERS)
            rows=rows.loc[rows.date.ge(pd.Timestamp(plan['start']))&rows.date.le(pd.Timestamp(plan['comparison_end']))]
            assert pd.DatetimeIndex(rows.date).as_unit('ns').equals(grid)
            expected=pd.Series(flag,index=pd.DatetimeIndex(source.date)).shift(1).reindex(grid).fillna(0)
            np.testing.assert_array_equal(rows.enter_long,expected)
            assert not rows.exit_long.any() and not rows.enter_short.any() and not rows.exit_short.any()
            for cutoff in ['2025-03-01','2026-01-01','2026-06-01']:
                before=source.date<pd.Timestamp(cutoff,tz='UTC')
                changed=source.copy()
                changed.loc[~before,['open','high','low','close']]*=7
                changed.loc[~before,'volume']*=5
                for altered in [changed,source.loc[before].copy()]:
                    recomputed=strategy.advise_indicators(altered,{'pair':pair})
                    for field in ['volatility_kcw','volatility_dcp']:
                        actual=recomputed.loc[recomputed.date<pd.Timestamp(cutoff,tz='UTC'),field]
                        np.testing.assert_array_equal(actual,indicators[pair].loc[before,field])
                        prefix_cells+=len(actual)
                    reflag=strategy.ft_advise_signals(recomputed,{'pair':pair})
                    expected_flag=flag[before.to_numpy()]
                    np.testing.assert_array_equal(reflag.enter_long.fillna(0).iloc[:len(expected_flag)],expected_flag)
                    prefix_cells+=len(expected_flag)
            indicators[pair].to_parquet(out/(pair.split('/')[0]+'_native_indicators.parquet'),index=False)
            rows.to_parquet(out/(pair.split('/')[0]+'_native_converted.parquet'),index=False)
            checks.append({'pair':pair,'source_rows':len(source),'target_rows':len(rows),
                           'finite_source_indicator_cells':int(np.isfinite(kcw).sum()+np.isfinite(dcp).sum()),
                           'source_flags_after_native_delay_exact':True})
        result={'passed':True,'native_constructor':True,'native_strategy_init':True,
                'actual_buy_params':actual_params,'output_directory':str(out),'startup':100,'source_timeframe':'4h',
                'checks':checks,'actual_prefix_cells_including_repeated_prefixes':prefix_cells,
                'financial_start':False,'funded_accounts':0}
        (HERE/'preflight.json').write_text(json.dumps(result,indent=2)+'\n')
        print('Preflight passed',checks,'prefix cells',prefix_cells,flush=True)
    finally:
        if account is not None:account.cleanup()
        exchange.close()


if __name__=='__main__':
    main()
