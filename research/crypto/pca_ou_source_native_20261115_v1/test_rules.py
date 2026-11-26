"""Past-window, dimensional and missing-input invariants, no economic tuning."""
import numpy as np
import pandas as pd
import pytest
from bootstrap import boot

boot()
from freqtrade.data.pca_ou import pca_ou_diagnostics, pca_ou_window


def data():
    rng=np.random.default_rng(9041)
    innovations=rng.normal(0,.003,(330,6))
    return pd.DataFrame(100*np.exp(innovations.cumsum(axis=0)),
        index=pd.date_range('2025-01-01',periods=330,freq='h',tz='UTC'),columns=list('ABCDEF'))


@pytest.mark.parametrize('fault',['missing','duplicate','nan','zero','negative','timezone','boolean','strings','constant','warmup'])
def test_unsupported_input_does_not_become_zero_exposure(fault):
    frame=data();decision=frame.index[300]
    if fault=='missing':frame=frame.drop(frame.index[200])
    if fault=='duplicate':frame.index=pd.DatetimeIndex([frame.index[0],*frame.index[:-1]])
    if fault=='nan':frame.iloc[200,0]=np.nan
    if fault=='zero':frame.iloc[200,0]=0
    if fault=='negative':frame.iloc[200,0]=-1
    if fault=='timezone':frame.index=frame.index.tz_localize(None)
    if fault=='boolean':frame['A']=True
    if fault=='strings':frame=frame.astype(str)
    if fault=='constant':frame['A']=100
    if fault=='warmup':decision=frame.index[200]
    with pytest.raises(ValueError):pca_ou_diagnostics(frame,[decision])


def test_only_completed_past_window_and_exact_prefix():
    frame=data();decision=frame.index[300]
    observed=pca_ou_diagnostics(frame,[decision])
    altered=frame.copy();altered.loc[decision:]*=2.5
    for other in [altered,frame.loc[frame.index<decision]]:
        result=pca_ou_diagnostics(other,[decision])
        for key in observed:pd.testing.assert_frame_equal(observed[key],result[key],check_exact=True)


def test_price_unit_invariance_and_reference_terminal_recentering():
    frame=data();decision=frame.index[300]
    a=pca_ou_diagnostics(frame,[decision])
    scaled=frame*np.arange(1,7)
    b=pca_ou_diagnostics(scaled,[decision])
    np.testing.assert_allclose(a['pca_ou_reversal'],b['pca_ou_reversal'],atol=1e-8,equal_nan=True)
    assert np.nanmax(np.abs(a['terminal_cumulative_residual']))<1e-12


@pytest.mark.parametrize('shape',[(239,6),(241,6),(240,2)])
def test_source_window_is240prices_not240returns(shape):
    with pytest.raises(ValueError):pca_ou_window(np.ones(shape))


def test_timestamp_units_preserve_observation_instants():
    frame=data();decision=frame.index[300]
    alternate=frame.copy();alternate.index=alternate.index.as_unit('us')
    a,b=pca_ou_diagnostics(frame,[decision]),pca_ou_diagnostics(alternate,[decision])
    for key in a:pd.testing.assert_frame_equal(a[key],b[key],check_exact=True)
