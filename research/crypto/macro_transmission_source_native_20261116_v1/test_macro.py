"""Macro quote identity, publication assumptions and actual native provider clocks."""
from dataclasses import replace
from datetime import UTC,date,datetime,timedelta
import json
import math

import pytest

from bootstrap import boot
boot()
from freqtrade.data.macro_transmission import (MacroClock,MacroInputError,MacroQuote,
    MacroQuoteSnapshot,macro_headwind_at,parse_yahoo_macro)
from freqtrade.data.macro_transmission_provider import MacroTransmissionDataProvider
from freqtrade.enums import RunMode

SEEN=datetime(2026,11,16,tzinfo=UTC)
AT=datetime(2025,1,22,6,tzinfo=UTC)


def snapshot(symbol='^TNX',values=None):
    values=values if values is not None else list(range(1,32))
    rows=tuple(MacroQuote(date(2025,1,1)+timedelta(days=i),
        datetime(2025,1,1,15,tzinfo=UTC)+timedelta(days=i),v) for i,v in enumerate(values))
    return MacroQuoteSnapshot(symbol,'America/Chicago' if symbol=='^TNX' else 'America/New_York',SEEN,rows)


def test_source_fixed_ten_calendar_day_and_gate_default_reduction():
    x=macro_headwind_at(snapshot(),snapshot('DX-Y.NYB'),at=AT,clock=MacroClock.NEXT_DAY_06UTC_MODEL)
    assert x['status']=='MODEL_ONLY' and x['headwind'] is True and x['target_fraction']==.25
    assert x['yield_change_percentage_points']==10
    assert x['dollar_log_change']==pytest.approx(math.log(21)-math.log(11))


@pytest.mark.parametrize('leg',['yield','dollar','both'])
def test_headwind_requires_both_strictly_positive_changes(leg):
    y=snapshot(values=[4.]*31) if leg in ['yield','both'] else snapshot()
    d=snapshot('DX-Y.NYB',values=[100.]*31) if leg in ['dollar','both'] else snapshot('DX-Y.NYB')
    x=macro_headwind_at(y,d,at=AT,clock=MacroClock.NEXT_DAY_06UTC_MODEL)
    assert x['headwind'] is False and x['target_fraction']==1


def test_snapshot_never_backdates_and_current_snapshot_can_describe_old_endpoints_now():
    a=AT+timedelta(hours=1)
    y=replace(snapshot(),observed_at=a,quotes=snapshot().quotes[:21]);d=replace(snapshot('DX-Y.NYB'),observed_at=a,quotes=snapshot('DX-Y.NYB').quotes[:21])
    assert macro_headwind_at(y,d,at=AT)['status']=='UNKNOWN'
    assert macro_headwind_at(y,d,at=a)['status']=='KNOWN_SNAPSHOT'


def test_source_bar_start_timestamp_does_not_make_close_available():
    s=snapshot();a=datetime(2025,1,12,5,59,59,tzinfo=UTC)
    old=s.level_at(a,clock=MacroClock.NEXT_DAY_06UTC_MODEL)
    new=s.level_at(a+timedelta(seconds=1),clock=MacroClock.NEXT_DAY_06UTC_MODEL)
    assert old['session_day']=='2025-01-10' and new['session_day']=='2025-01-11'


def test_null_row_stays_null_and_carry_is_dated_not_an_observed_new_close():
    values=list(range(1,32));values[20]=None;s=snapshot(values=values)
    x=s.level_at(AT,clock=MacroClock.NEXT_DAY_06UTC_MODEL)
    assert s.quotes[20].close is None
    assert x['session_day']=='2025-01-20' and x['value']==20
    assert x['later_raw_null_rows']==1 and x['age_hours']==24


def test_stale_and_missing_endpoints_do_not_become_full_or_cash():
    s=snapshot(values=[1]+[None]*30)
    x=macro_headwind_at(s,snapshot('DX-Y.NYB'),at=AT,clock=MacroClock.NEXT_DAY_06UTC_MODEL)
    assert x['status']=='UNKNOWN' and x['headwind'] is x['target_fraction'] is None


def test_negative_yield_allowed_but_nonpositive_dollar_is_invalid():
    snapshot(values=[-1.]*31)
    with pytest.raises(MacroInputError):snapshot('DX-Y.NYB',values=[0.]*31)


@pytest.mark.parametrize('fault',['nan','infinity','bool','duplicate','reverse','naive','wrong_day','wrong_tz'])
def test_invalid_identity_units_and_source_clock_rejected(fault):
    s=snapshot();rows=list(s.quotes)
    if fault in ['nan','infinity','bool']:rows[10]=replace(rows[10],close={'nan':float('nan'),'infinity':float('inf'),'bool':True}[fault])
    if fault=='duplicate':rows[10]=rows[9]
    if fault=='reverse':rows=rows[::-1]
    if fault=='naive':rows[10]=replace(rows[10],provider_bar_label=datetime(2025,1,11))
    if fault=='wrong_day':rows[10]=replace(rows[10],session_day=date(2025,1,10))
    with pytest.raises(MacroInputError):replace(s,quotes=tuple(rows),timezone='UTC' if fault=='wrong_tz' else s.timezone)


def payload():
    return {'chart':{'error':None,'result':[{'meta':{'symbol':'^TNX','instrumentType':'INDEX',
        'dataGranularity':'1d','exchangeTimezoneName':'America/Chicago'},
        'timestamp':[1735743600],'indicators':{'quote':[{'close':[4.5]}]}}]}}


@pytest.mark.parametrize('fault',['html','error','symbol','frequency','milliseconds','length'])
def test_raw_source_error_and_identity_contract(fault):
    x=payload();r=x['chart']['result'][0]
    if fault=='html':raw=b'<html>403</html>'
    else:
        if fault=='error':x['chart']['error']={'description':'not found'}
        if fault=='symbol':r['meta']['symbol']='DGS10'
        if fault=='frequency':r['meta']['dataGranularity']='1wk'
        if fault=='milliseconds':r['timestamp'][0]*=1000
        if fault=='length':r['indicators']['quote'][0]['close']=[]
        raw=json.dumps(x).encode()
    with pytest.raises(MacroInputError):parse_yahoo_macro(raw,symbol='^TNX',observed_at=SEEN)


def test_raw_parser_preserves_none_and_epoch_source_label():
    x=payload();x['chart']['result'][0]['indicators']['quote'][0]['close']=[None]
    s=parse_yahoo_macro(json.dumps(x).encode(),symbol='^TNX',observed_at=SEEN)
    assert s.quotes[0].close is None and s.quotes[0].provider_bar_label.tzinfo is not None


def test_native_provider_future_perturbation_truncation_and_detached_result():
    config={'runmode':RunMode.BACKTEST,'research_macro_inputs':True,'timeframe':'1h'}
    y,d=snapshot(),snapshot('DX-Y.NYB');a=AT-timedelta(days=1)
    future=tuple(replace(row,close=999.) if row.session_day>=date(2025,1,21) else row for row in y.quotes)
    ys=[y,replace(y,quotes=future),replace(y,quotes=y.quotes[:20])]
    results=[]
    for item in ys:
        p=MacroTransmissionDataProvider(config,item,d,clock=MacroClock.NEXT_DAY_06UTC_MODEL)
        with pytest.raises(MacroInputError):p.macro_headwind()
        p._set_dataframe_max_date(a);results.append(p.macro_headwind())
    assert results[0]==results[1]==results[2]
    p._set_dataframe_max_date(AT)
    result=p.macro_headwind();result['endpoints'][0]['value']=-999
    assert p.macro_headwind()['endpoints'][0]['value']!=-999


@pytest.mark.parametrize('runmode',[RunMode.LIVE,RunMode.DRY_RUN])
def test_live_and_paper_input_use_rejected(runmode):
    with pytest.raises(MacroInputError):MacroTransmissionDataProvider({'runmode':runmode,'research_macro_inputs':True},snapshot(),snapshot('DX-Y.NYB'))


def test_source_future_bar_cannot_precede_actual_receipt():
    with pytest.raises(MacroInputError):replace(snapshot(),observed_at=datetime(2024,12,31,tzinfo=UTC))


@pytest.mark.parametrize('age',[0,-1,True])
def test_age_contract_cannot_be_disabled_or_boolean(age):
    with pytest.raises(MacroInputError):snapshot().level_at(AT,max_age_hours=age)


def test_current_knowledge_cannot_select_a_future_endpoint():
    with pytest.raises(MacroInputError):snapshot().level_at(AT,known_at=AT-timedelta(seconds=1))


def test_nominal_yield_and_dollar_roles_cannot_be_swapped():
    with pytest.raises(MacroInputError):macro_headwind_at(snapshot('DX-Y.NYB'),snapshot(),at=AT)


def test_native_cache_reset_cannot_reuse_previous_observation_clock():
    p=MacroTransmissionDataProvider({'runmode':RunMode.BACKTEST,'research_macro_inputs':True},
        snapshot(),snapshot('DX-Y.NYB'),clock=MacroClock.NEXT_DAY_06UTC_MODEL)
    p._set_dataframe_max_date(AT);assert p.macro_headwind()['status']=='MODEL_ONLY'
    p.clear_cache()
    with pytest.raises(MacroInputError):p.macro_headwind()
