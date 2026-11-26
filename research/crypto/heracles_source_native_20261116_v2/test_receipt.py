import pandas as pd
import pytest
from CaptureRules import signal_receipt


def frame():
    return pd.DataFrame({'date':pd.date_range('2025-01-01',periods=3,freq='4h',tz='UTC'),
                         'open':100.,'high':102.,'low':98.,'close':101.,'volume':10.,
                         'enter_long':[1.,None,1.],'exit_long':0.})


def test_native_optional_short_flags_have_same_receipt_as_explicit_zero():
    original=frame()
    first=signal_receipt(original,'HeraclesNative')
    second=signal_receipt(original.assign(enter_short=0.,exit_short=0.),'HeraclesNative')
    assert first['sha256']==second['sha256']
    assert first['optional_signal_columns_neutralized']==['enter_short','exit_short']
    assert 'enter_short' not in original


def test_real_quotes_cannot_be_neutralized_in_readonly_receipt():
    with pytest.raises(ValueError,match='OHLCV'):
        signal_receipt(frame().drop(columns='close'),'HeraclesNative')
