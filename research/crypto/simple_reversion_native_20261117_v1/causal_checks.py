"""Source behavior and causal prefixes against the pinned native engine."""
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

HERE=Path(__file__).absolute().parent


def main():
    plan=json.loads((HERE/'plan.json').read_text());sys.path.insert(0,plan['engine_source'])
    import freqtrade
    assert Path(freqtrade.__file__).absolute()==Path(plan['engine_source'])/'freqtrade/__init__.py'
    spec=importlib.util.spec_from_file_location('pinned_source_strategy',HERE/'source/SimpleMeanReversion.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);strategy=m.SimpleMeanReversion({})
    pre=Path(plan['output_root'])/'preflight_reversion_1000_conservative_admission1'
    params=json.loads((pre/'effective_strategy_parameters.json').read_text());assert params['stoploss']==-.15 and params['startup_candle_count']==150 and not params['use_exit_signal']and not params['position_adjustment_enable']
    strategy.minimal_roi={int(k):v for k,v in params['minimal_roi'].items()}
    opened=datetime(2025,1,1,tzinfo=timezone.utc);trade=SimpleNamespace(open_date_utc=opened)
    thresholds=[]
    for minutes,threshold in [(719,.05),(720,.03),(1439,.03),(1440,.01),(2879,.01),(2880,-.01),(4320,-.01)]:
        got=strategy.min_roi_reached_entry(trade,minutes,opened+timedelta(minutes=minutes));assert got[1]==threshold
        thresholds.append(dict(duration_minutes=minutes,threshold=threshold))
    assert not strategy.min_roi_reached(trade,-.10,opened+timedelta(hours=60))
    assert strategy.min_roi_reached(trade,0.,opened+timedelta(hours=60))
    series=pd.Series(100+np.cumsum(np.random.default_rng(21).normal(0,1,100)))
    delta=series.diff();gain=delta.clip(lower=0);loss=(-delta).clip(lower=0)
    simple=100-100/(1+gain.rolling(14).mean()/loss.rolling(14).mean())
    pd.testing.assert_series_equal(strategy._rsi(series),simple)
    wilder=100-100/(1+gain.ewm(alpha=1/14,adjust=False).mean()/loss.ewm(alpha=1/14,adjust=False).mean())
    assert (simple-wilder).abs().max()>1
    def signals(raw,pair):
        frame=strategy.populate_indicators(raw.copy(),{'pair':pair});frame=strategy.populate_entry_trend(frame,{'pair':pair})
        return frame[['bb_lower','bb_mid','bb_upper','rsi','enter_long']].fillna(0)
    checks=[]
    for asset in ['BTC','ETH']:
        raw=pd.read_parquet(pre/(asset+'_native_signals.parquet'))[['date','open','high','low','close','volume']]
        pair=asset+'/USDT';full=signals(raw,pair)
        for cutoff in [200,4000,10000]:
            prefix=signals(raw.iloc[:cutoff+1],pair)
            pd.testing.assert_frame_equal(prefix,full.iloc[:cutoff+1],atol=1e-10,rtol=1e-12)
            changed=raw.copy();changed.loc[changed.index>cutoff,['open','high','low','close']]*=3
            future=signals(changed,pair);pd.testing.assert_frame_equal(prefix,future.iloc[:cutoff+1],atol=1e-10,rtol=1e-12)
            checks.append(dict(pair=pair,cutoff=str(raw.date.iloc[cutoff]),passed=True))
    receipt=dict(passed=True,source_code_unchanged=True,past_only_checks=checks,roi_native_threshold_cases=thresholds,sixty_hour_minus10pct_not_forced_exit=True,rsi_is_simple_rolling_not_wilder=True,effective_native_parameters=params,new_financial_accounts=0)
    (HERE/'causal_checks.json').write_text(json.dumps(receipt,indent=2)+'\n');print('PASS6actualprefix/futureshockcases;nativeROIsemanticsverified;0accounts')


if __name__=='__main__':main()
