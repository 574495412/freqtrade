"""Source binding, prefix causality, frozen defaults and invalid-input checks."""
import numpy as np
import pandas as pd
import pytest

from MAMAInputs import phase_features
from source_reference import source_mama


def frame(values):
    return pd.DataFrame(dict(date=pd.date_range('2024-01-01',periods=len(values),freq='h',tz='UTC'),close=values))


@pytest.mark.parametrize('kind',['constant','ramp','cycle','random'])
def test_pinned_source_double_c_matches_installed_library(kind):
    n=2500
    if kind=='constant':v=np.full(n,100.)
    if kind=='ramp':v=np.linspace(100,1000,n)
    if kind=='cycle':v=100+10*np.sin(np.arange(n)/7)
    if kind=='random':v=np.exp(np.random.default_rng(732).normal(0,.01,n).cumsum())*100
    result=phase_features(frame(v));expected=source_mama(v)
    for name,wanted in zip(['mama','fama'],expected,strict=True):
        np.testing.assert_allclose(result[name],wanted,atol=1e-7,rtol=1e-10,equal_nan=True)
    assert result.mama.iloc[:32].isna().all() and result.mama.iloc[32:].notna().all()


@pytest.mark.parametrize('cut',[513,1000,1900])
def test_actual_refit_future_perturbation_and_truncation(cut):
    v=np.exp(np.random.default_rng(128).normal(0,.01,2500).cumsum())*100
    full=phase_features(frame(v));changed=v.copy();changed[cut:]*=99
    altered=phase_features(frame(changed));prefix=phase_features(frame(v[:cut]))
    for column in ['mama','fama']:
        np.testing.assert_array_equal(full[column].iloc[:cut],altered[column].iloc[:cut])
        np.testing.assert_array_equal(full[column].iloc[:cut],prefix[column])


@pytest.mark.parametrize('fault',['nan','zero','negative','infinity','duplicate','reversed','naive','nat'])
def test_invalid_inputs_rejected_without_imputation(fault):
    f=frame(np.full(100,100.))
    if fault in ['nan','zero','negative','infinity']:f.loc[50,'close']={'nan':np.nan,'zero':0,'negative':-1,'infinity':np.inf}[fault]
    if fault=='duplicate':f.loc[50,'date']=f.loc[49,'date']
    if fault=='reversed':f=f.iloc[::-1]
    if fault=='naive':f.date=f.date.dt.tz_localize(None)
    if fault=='nat':f.loc[50,'date']=pd.NaT
    with pytest.raises(ValueError):phase_features(f)
