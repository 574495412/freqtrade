"""Meaningful source-window, visibility, input and financial admission boundaries."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from bootstrap import boot

boot()
from freqtrade.data.max_lottery import daily_max_components, hourly_completed_daily_view
from freqtrade.enums import RunMode
from MaxInputs import MaxInputs


def sample():
    index = pd.date_range('2024-01-01',periods=70,freq='1D',tz='UTC')
    returns = np.array([.001*(i%9-3)for i in range(70)])
    return pd.DataFrame({'A':100*np.cumprod(1+returns),'B':200*np.cumprod(1-returns/2)},index=index)


def test_exact28returns_and_source_momentum_skip_last_week():
    prices = sample(); parts = daily_max_components(prices)
    assert parts['max28'].iloc[:28].isna().all().all()
    for end in [28,29,65]:
        for pair in prices:
            r = [prices[pair].iloc[i]/prices[pair].iloc[i-1]-1 for i in range(end-27,end+1)]
            assert parts['max28'][pair].iloc[end] == max(r)
            assert parts['mom21_skip7'][pair].iloc[end] == prices[pair].iloc[end-7]/prices[pair].iloc[end-28]-1


@pytest.mark.parametrize('fault',['missing','duplicate','unsorted','timezone','nondaily','zero','nan','inf','duplicate_asset'])
def test_bad_input_does_not_get_filled_or_dropped(fault):
    prices = sample()
    if fault=='missing':prices=prices.drop(prices.index[30])
    if fault=='duplicate':prices=pd.concat([prices.iloc[:30],prices.iloc[29:]])
    if fault=='unsorted':prices=prices.iloc[::-1]
    if fault=='timezone':prices.index=prices.index.tz_convert('America/New_York')
    if fault=='nondaily':prices.index+=pd.Timedelta(hours=1)
    if fault=='zero':prices.iloc[30,0]=0
    if fault=='nan':prices.iloc[30,0]=np.nan
    if fault=='inf':prices.iloc[30,0]=np.inf
    if fault=='duplicate_asset':prices.columns=['A','A']
    with pytest.raises(ValueError):daily_max_components(prices)


def test_future_perturbation_truncation_and_quote_scale_invariance():
    prices=sample(); original=daily_max_components(prices)
    changed=prices.copy();changed.iloc[50:]*=100
    altered=daily_max_components(changed);short=daily_max_components(prices.iloc[:50]);scaled=daily_max_components(prices*10)
    for key in original:
        pd.testing.assert_frame_equal(original[key].iloc[:50],altered[key].iloc[:50],check_exact=True)
        pd.testing.assert_frame_equal(original[key].iloc[:50],short[key],check_exact=True)
        np.testing.assert_allclose(original[key],scaled[key],rtol=1e-9,atol=1e-11,equal_nan=True)


def test_hourly_visibility_rolls_only_after_daily_close():
    prices=sample();parts=daily_max_components(prices)
    hours=pd.date_range(prices.index[40]-pd.Timedelta(hours=1),periods=3,freq='1h')
    view=hourly_completed_daily_view(parts,hours)
    for name in parts:
        assert view[name].iloc[0].equals(parts[name].iloc[38])
        assert view[name].iloc[1].equals(parts[name].iloc[39])
        assert view[name].iloc[1].equals(view[name].iloc[2])


@pytest.mark.parametrize('fault',['gap','fraction','component','assets'])
def test_hourly_or_component_grid_cannot_silently_change(fault):
    parts=daily_max_components(sample());hours=pd.date_range('2024-02-20',periods=5,freq='1h',tz='UTC')
    if fault=='gap':hours=hours.delete(2)
    if fault=='fraction':hours+=pd.Timedelta(minutes=1)
    if fault=='component':parts.pop('max28')
    if fault=='assets':parts['raw28']=parts['raw28'][['B','A']]
    with pytest.raises(ValueError):hourly_completed_daily_view(parts,hours)


def test_flat_observed_price_is_zero_max_not_unknown_input():
    prices=sample()*0+100
    parts=daily_max_components(prices)
    assert parts['max28'].iloc[28:].eq(0).all().all()
    assert parts['skew28'].iloc[28:].eq(0).all().all()


@pytest.mark.parametrize('mode',[RunMode.LIVE,RunMode.DRY_RUN,RunMode.OTHER])
def test_service_modes_rejected_before_inputs(mode):
    owner=object.__new__(MaxInputs);owner.config={'research_diagnostic_only':True,'dry_run':True,'runmode':mode}
    with pytest.raises(ValueError,match='diagnostic only'):owner.bot_start()


def test_native_financial_start_is_always_closed_even_if_caller_skips_bot_start():
    with pytest.raises(ValueError,match='not admitted'):object.__new__(MaxInputs).ft_bot_start()
