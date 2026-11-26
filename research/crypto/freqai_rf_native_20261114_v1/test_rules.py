"""Maturity, original categories, confidence boundaries and mode constraints."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent))
from ModelRules import intents,targets


def test_unknown_future_is_not_classified_as_down():
    frame=pd.DataFrame({'close':np.arange(1.,51.)})
    out=targets(frame)
    assert out.iloc[:-20].eq('up').all() and out.iloc[-20:].isna().all()
    frame.loc[25,'close']=np.nan;out=targets(frame)
    assert pd.isna(out.iloc[5]) and pd.isna(out.iloc[25])


def test_exact_confidence_boundaries_and_no_immediate_reversal():
    up=np.array([.6,.5,.49,.3,.5,.51,.7]);down=1-up
    frame=pd.DataFrame(dict(date=pd.date_range('2025-01-01',periods=7,freq='h',tz='UTC'),up=up,down=down,do_predict=1))
    np.testing.assert_array_equal(intents(frame,'2025-01-01T00:00Z'),[1,1,0,-1,-1,0,1])


@pytest.mark.parametrize('fault',['nan','invalid','suppressed','outofrange'])
def test_unavailable_prediction_never_turns_into_flat(fault):
    f=pd.DataFrame(dict(date=pd.date_range('2025-01-01',periods=1,freq='h',tz='UTC'),up=.6,down=.4,do_predict=1))
    if fault=='nan':f['up']=np.nan
    elif fault=='invalid':f['down']=.7
    elif fault=='outofrange':f['up']=1.1;f['down']=-.1
    else:f['do_predict']=0
    with pytest.raises(ValueError):intents(f,'2025-01-01T00:00Z')


def test_training_warmup_is_explicit_flat_not_a_synthetic_prediction():
    f=pd.DataFrame(dict(date=pd.date_range('2024-12-31T23:00Z',periods=2,freq='h'),up=[np.nan,.6],down=[np.nan,.4],do_predict=[0,1]))
    np.testing.assert_array_equal(intents(f,'2025-01-01T00:00Z'),[0,1])
