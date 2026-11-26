import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'dependencies'))
sys.path.insert(0,str(HERE/'sources'))
sys.path.insert(0,'/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')
from Heracles import Heracles
from HeraclesNative import HeraclesNative

from freqtrade.enums import RunMode


def data():
    x=np.arange(100)
    p=100+x*.1+np.sin(x)
    return pd.DataFrame({'date':pd.date_range('2025-01-01',periods=100,freq='4h',tz='UTC'),
                         'open':p,'high':p+2,'low':p-2,'close':p,'volume':100.+x})


def adapter():
    return HeraclesNative({'runmode':RunMode.BACKTEST,'dry_run':True,
                           'research_heracles_ohlcv_adapter':True})


def test_private_nullable_precision_columns_do_not_delete_real_source_bars():
    source=data()
    expected=Heracles({}).populate_indicators(source.copy(),{})
    decorated=source.assign(open_count=np.nan,high_count=np.nan,low_count=np.nan,
                            close_count=np.nan,max_count=np.nan)
    result=adapter().populate_indicators(decorated,{})
    assert len(result)==len(source)
    for key in ['volatility_kcw','volatility_dcp']:
        np.testing.assert_array_equal(result[key],expected[key])
    assert result.open_count.isna().all()


def test_helper_values_never_change_the_strategy_indicator_input():
    frame=data().assign(max_count=np.arange(100))
    first=adapter().populate_indicators(frame,{})
    frame.loc[30:,'max_count']=np.nan
    second=adapter().populate_indicators(frame,{})
    for key in ['volatility_kcw','volatility_dcp']:
        np.testing.assert_array_equal(first[key],second[key])


def test_real_ohlcv_rejection_cannot_be_replaced_with_zero_or_compressed_history():
    frame=data()
    frame.loc[20,'volume']=0
    with pytest.raises(ValueError,match='rejected an OHLCV row'):
        adapter().populate_indicators(frame,{})


def test_undeclared_input_or_non_backtest_rejects():
    with pytest.raises(ValueError,match='namespace'):
        adapter().populate_indicators(data().assign(future_target=1.),{})
    with pytest.raises(ValueError,match='BACKTEST'):
        HeraclesNative({}).populate_indicators(data(),{})
