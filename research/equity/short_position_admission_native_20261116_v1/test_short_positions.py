"""SFC schema, actual callback clocks and inventory/flow separation boundaries."""
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import hashlib

import pandas as pd
import pytest

from bootstrap import boot

boot()
from freqtrade.data.equity_short_positions import HEADER, parse_sfc_csv, reported_position
from freqtrade.data.equity_short_positions_provider import EquityShortPositionDataProvider
from freqtrade.data.equity_provider import EquitySchedule
from freqtrade.data.history.equity import EquityAdmissionError, EquitySessionContract
from freqtrade.enums import RunMode

DAY = date(2025, 3, 7)
PUB = datetime(2025, 3, 14, 8, tzinfo=UTC)
SEEN = PUB + timedelta(minutes=1)
URL = 'https://www.sfc.hk/example.csv'


def body(rows=None):
    # Synthetic source-format rows, not a downloaded regulator historical file.
    return (','.join(HEADER) + '\n' + (rows if rows is not None else
        '07/03/2025,700,SYNTHETIC,123,456.75\n07/03/2025,28,ZERO,0,0\n07/03/2025,999,NO PRICE,10,n.a.\n')).encode()


def report(raw=None, **kwargs):
    raw = body() if raw is None else raw
    args = dict(reporting_date=DAY, source_url=URL,
        source_sha256=hashlib.sha256(raw).hexdigest(), published_at=PUB, observed_at=SEEN)
    return parse_sfc_csv(raw, **(args | kwargs))


def provider(reports=None, pair='00700/HKD', market='XHKG'):
    labels = ('2025-03-14', '2025-03-17')
    index = pd.to_datetime(labels, utc=True)
    schedule = pd.DataFrame({'open':index + pd.Timedelta(hours=1.5),
        'close':index + pd.Timedelta(hours=8),'break_start':pd.NaT,'break_end':pd.NaT},index=index)
    s = EquitySchedule(EquitySessionContract(pair,market,labels), schedule)
    return EquityShortPositionDataProvider({'runmode':RunMode.BACKTEST,'timeframe':'1d'},
        {pair:s}, [report()] if reports is None else reports)


def test_units_are_reported_inventory_not_flow_or_fraction():
    r = report(); row = reported_position(r,'00700',at=SEEN)
    assert row['reported_shares'] == 123 and row['reported_hkd_value'] == Decimal('456.75')
    assert 'short_interest_percent' not in row and 'short_sale_volume' not in row


def test_explicit_zero_is_retained_but_absence_remains_unknown():
    assert reported_position(report(),'00028',at=SEEN)['reported_shares'] == 0
    assert reported_position(report(),'00123',at=SEEN)['reported_shares'] is None
    assert reported_position(report(),'00123',at=SEEN)['status'] == 'unknown'


def test_missing_price_does_not_remove_known_reported_shares():
    row=reported_position(report(),'00999',at=SEEN)
    assert row['reported_shares']==10 and row['reported_hkd_value'] is None


@pytest.mark.parametrize('field',['published_at','observed_at'])
def test_missing_historical_clock_keeps_numeric_report_unavailable(field):
    assert reported_position(report(**{field:None}),'00700',at=SEEN)['status']=='unknown'


def test_current_retrieval_does_not_backdate_historical_availability():
    r=report(observed_at=datetime(2026,11,16,tzinfo=UTC))
    assert reported_position(r,'00700',at=SEEN)['status']=='unknown'


@pytest.mark.parametrize('rows',[
    '07/03/2025,700,X,1,1\n07/03/2025,00700,X,2,2\n',
    '06/03/2025,700,X,1,1\n','07/03/2025,700,X,-1,1\n',
    '07/03/2025,700,X,1.1,1\n','07/03/2025,700,X,n.a.,1\n',
    '07/03/2025,700,X,1,-1\n','07/03/2025,700,X,1,NaN\n',
    '07/03/2025,0,X,1,1\n','07/03/2025,700/X,X,1,1\n',
    '07/03/2025,700,X,1\n','07/03/2025,700,,1,1\n',''])
def test_invalid_or_duplicate_source_rows_rejected(rows):
    with pytest.raises(EquityAdmissionError): report(body(rows))


@pytest.mark.parametrize('raw',[b'<html>403 forbidden</html>',b'',b'\xff'])
def test_error_html_empty_and_encoding_never_become_reports(raw):
    with pytest.raises(EquityAdmissionError): report(raw)


def test_source_byte_identity_mandatory():
    with pytest.raises(EquityAdmissionError): report(source_sha256='0'*64)


@pytest.mark.parametrize('kwargs',[
    {'published_at':datetime(2025,3,7,8,tzinfo=UTC)},
    {'observed_at':PUB-timedelta(seconds=1)},
    {'published_at':datetime(2025,3,14)},
    {'published_at':pd.NaT}, {'source_url':'https://example.com/source.csv'}])
def test_clock_order_timezone_reporting_lag_and_original_host(kwargs):
    with pytest.raises(EquityAdmissionError): report(**kwargs)


def test_native_callback_clock_future_perturbation_truncation_and_copy():
    p=provider()
    with pytest.raises(EquityAdmissionError):p.get_reported_short_position('00700/HKD',DAY)
    altered=replace(report(),positions=(replace(report().positions[0],shares=999999),))
    variants=[p,provider([altered]),provider([])]
    for v in variants:v._set_dataframe_max_date(PUB)
    assert len({str(v.get_reported_short_position('00700/HKD',DAY)) for v in variants})==1
    p._set_dataframe_max_date(SEEN)
    value=p.get_reported_short_position('00700/HKD',DAY)
    assert value['reported_shares']==123
    value['reported_shares']=-1
    assert p.get_reported_short_position('00700/HKD',DAY)['reported_shares']==123


def test_exact_target_report_does_not_fallback_to_an_older_value():
    p=provider();p._set_dataframe_max_date(SEEN+timedelta(days=7))
    assert p.get_reported_short_position('00700/HKD',DAY+timedelta(days=7))['status']=='unknown'


def test_duplicate_report_revision_requires_explicit_policy():
    with pytest.raises(EquityAdmissionError):provider([report(),report()])


@pytest.mark.parametrize('pair,market',[('700/HKD','XHKG'),('ABC/USD','XNYS')])
def test_no_implicit_alias_or_cross_market_application(pair,market):
    p=provider(pair=pair,market=market);p._set_dataframe_max_date(SEEN)
    with pytest.raises(EquityAdmissionError):p.get_reported_short_position(pair,DAY)
