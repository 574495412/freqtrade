"""Direction/strength separation, complete windows and completed-bar visibility."""
import numpy as np
import pandas as pd
import pytest

from bootstrap import boot

boot()
from freqtrade.data.fip_path import daily_fip_components, hourly_completed_fip_view
from FipInputs import FipInputs


def prices(n=350):
    index=pd.date_range('2024-01-01',periods=n,freq='D',tz='UTC')
    return pd.DataFrame({'UP':100*1.001**np.arange(n),'DOWN':100*.999**np.arange(n)},index=index)


def test_source_metrics_measure_both_up_and_down_strength_but_local_score_has_direction():
    result=daily_fip_components(prices())
    for w in (63,252):
        assert (result['source_original'+str(w)].iloc[-1]<0).all()
        assert (result['source_modified'+str(w)].iloc[-1]==1).all()
        assert result['signed_path'+str(w)].UP.iloc[-1]>0
        assert result['signed_path'+str(w)].DOWN.iloc[-1]<0


def test_equal_total_move_has_smaller_signed_score_when_arriving_in_one_jump():
    frame=prices(65)
    frame['GRADUAL']=100*1.002**np.arange(65)
    frame['JUMP']=100.;frame.loc[frame.index[-1],'JUMP']=100*1.002**63
    result=daily_fip_components(frame)
    assert result['pret63'].GRADUAL.iloc[-1]==pytest.approx(result['pret63'].JUMP.iloc[-1])
    assert result['signed_path63'].GRADUAL.iloc[-1]>result['signed_path63'].JUMP.iloc[-1]>0


def test_full_window_counts_zero_days_and_never_compresses_missing_returns():
    frame=prices();frame.loc[:,:]=100
    result=daily_fip_components(frame)
    for w in (63,252):
        assert result['pret'+str(w)].iloc[:w].isna().all().all()
        assert (result['pret'+str(w)].iloc[w:]==0).all().all()
        assert (result['signed_path'+str(w)].iloc[w:]==0).all().all()
    frame.iloc[100,0]=float('nan')
    with pytest.raises(ValueError):daily_fip_components(frame)


@pytest.mark.parametrize('fault',['negative','zero','inf','duplicate_date','missing_day','naive','midday','duplicate_pair'])
def test_input_contract_rejects_invalid_prices_or_axes(fault):
    frame=prices()
    if fault in ('negative','zero','inf'):frame.iloc[0,0]={'negative':-1,'zero':0,'inf':float('inf')}[fault]
    if fault=='duplicate_date':frame.index=pd.DatetimeIndex([frame.index[0],*frame.index[:-1]])
    if fault=='missing_day':frame=frame.drop(frame.index[100])
    if fault=='naive':frame.index=frame.index.tz_localize(None)
    if fault=='midday':frame.index+=pd.Timedelta(hours=1)
    if fault=='duplicate_pair':frame.columns=['UP','UP']
    with pytest.raises(ValueError):daily_fip_components(frame)


def test_future_values_and_truncation_leave_every_past_feature_unchanged():
    frame=prices(400);full=daily_fip_components(frame)
    changed=frame.copy();changed.iloc[300:]*=5
    altered=daily_fip_components(changed);short=daily_fip_components(frame.iloc[:300])
    for key in full:
        pd.testing.assert_frame_equal(full[key].iloc[:300],altered[key].iloc[:300],check_exact=True)
        pd.testing.assert_frame_equal(full[key].iloc[:300],short[key],check_exact=True)


def test_midnight_hour_uses_only_daily_bar_that_already_closed():
    frame=prices();parts=daily_fip_components(frame)
    hours=pd.date_range(frame.index[-2],periods=48,freq='h')
    result=hourly_completed_fip_view(parts,hours)
    assert result['pret63'].UP.iloc[0]==parts['pret63'].UP.iloc[-3]
    assert result['pret63'].UP.iloc[23]==parts['pret63'].UP.iloc[-3]
    assert result['pret63'].UP.iloc[24]==parts['pret63'].UP.iloc[-2]
    with pytest.raises(ValueError):hourly_completed_fip_view(parts,hours+pd.Timedelta(minutes=5))
    with pytest.raises(ValueError):hourly_completed_fip_view({'pret63':parts['pret63']},hours)


def test_diagnostic_cannot_become_a_funded_or_cash_control_account():
    strategy=FipInputs({})
    with pytest.raises(ValueError):strategy.bot_start()
    with pytest.raises(ValueError):strategy.ft_bot_start()
    with pytest.raises(ValueError):strategy.bot_loop_start()
    assert not strategy.confirm_trade_entry()
