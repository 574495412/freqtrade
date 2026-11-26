"""Unknown inputs, EPS identity, original-release and native callback boundaries."""
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from bootstrap import boot

boot()
from freqtrade.data.equity_earnings import EarningsValue, consensus_surprise
from freqtrade.data.equity_earnings_provider import EquityEarningsDataProvider
from freqtrade.data.equity_provider import EquitySchedule
from freqtrade.data.history.equity import EquityAdmissionError, EquitySessionContract
from freqtrade.enums import RunMode

AT = datetime(2025, 1, 30, 21, 30, tzinfo=UTC)


def observations(market='XNYS'):
    currency = 'USD' if market == 'XNYS' else 'HKD'
    values = dict(event_id='original-quarter-release', pair='ABC/' + currency, market=market,
        currency=currency, fiscal_end=date(2024, 12, 31), basis='GAAP diluted class A EPS',
        source_sha256='a' * 64, document_id='original-source-document')
    actual = EarningsValue(**values, role='original_actual', value=-.8,
        published_at=AT, observed_at=AT + timedelta(minutes=1))
    forecast = EarningsValue(**values, role='analyst_consensus', value=-1.,
        published_at=AT - timedelta(days=1), observed_at=AT - timedelta(hours=1))
    return actual, forecast


def provider(market='XNYS', records=None):
    a, f = observations(market)
    labels = ('2025-01-30', '2025-01-31')
    index = pd.to_datetime(labels, utc=True)
    hours = (14.5, 21) if market == 'XNYS' else (1.5, 8)
    schedule = pd.DataFrame({'open': index + pd.Timedelta(hours=hours[0]),
        'close': index + pd.Timedelta(hours=hours[1]),
        'break_start': pd.NaT, 'break_end': pd.NaT}, index=index)
    s = EquitySchedule(EquitySessionContract(a.pair, market, labels), schedule)
    return EquityEarningsDataProvider({'runmode': RunMode.BACKTEST, 'timeframe': '1d'},
        {a.pair: s}, records if records is not None else [(a, f)])


def test_negative_estimate_sign_is_preserved_and_source_percent_not_multiplied_twice():
    a, f = observations()
    result = consensus_surprise(a, f, at=AT + timedelta(minutes=1))
    assert result['status'] == 'known' and result['percent_surprise'] == pytest.approx(20)
    assert consensus_surprise(replace(a, value=100), f, at=AT + timedelta(minutes=1))['percent_surprise'] == 200


@pytest.mark.parametrize('missing', ['value', 'published_at', 'observed_at', 'source_sha256', 'document_id'])
def test_missing_consensus_is_retained_unknown_not_zero(missing):
    a, f = observations()
    result = consensus_surprise(a, replace(f, **{missing: None}), at=AT + timedelta(days=1))
    assert result['event_id'] == a.event_id and result['status'] == 'unknown'
    assert result['percent_surprise'] is None


@pytest.mark.parametrize('offset', [0, 1])
def test_same_clock_or_post_release_consensus_cannot_enter(offset):
    a, f = observations()
    f = replace(f, observed_at=AT + timedelta(seconds=offset))
    assert consensus_surprise(a, f, at=AT + timedelta(days=1))['status'] == 'unknown'


@pytest.mark.parametrize('fault', ['nan', 'infinity', 'near_zero', 'late_actual'])
def test_invalid_or_not_yet_observed_values_stay_unknown(fault):
    a, f = observations()
    if fault == 'nan': a = replace(a, value=float('nan'))
    if fault == 'infinity': a = replace(a, value=float('inf'))
    if fault == 'near_zero': f = replace(f, value=.009)
    if fault == 'late_actual': a = replace(a, observed_at=AT + timedelta(days=2))
    assert consensus_surprise(a, f, at=AT + timedelta(days=1))['status'] == 'unknown'


@pytest.mark.parametrize('field,value', [('basis', 'adjusted basic class B'),
    ('event_id', 'different-quarter'), ('fiscal_end', date(2024, 9, 30)), ('role', 'original_actual')])
def test_different_eps_basis_quarter_or_role_rejected(field, value):
    a, f = observations()
    with pytest.raises(EquityAdmissionError):
        consensus_surprise(a, replace(f, **{field: value}), at=AT + timedelta(days=1))


@pytest.mark.parametrize('market', ['XNYS', 'XHKG'])
def test_native_provider_clock_boundary_and_future_counterfactual(market):
    a, f = observations(market)
    p = provider(market)
    with pytest.raises(EquityAdmissionError): p.get_earnings_surprises(a.pair)
    p._set_dataframe_max_date(AT)
    assert p.get_earnings_surprises(a.pair) == []
    altered = provider(market, [(replace(a, value=100), f)])
    altered._set_dataframe_max_date(AT)
    assert altered.get_earnings_surprises(a.pair) == p.get_earnings_surprises(a.pair)
    p._set_dataframe_max_date(a.observed_at)
    result = p.get_earnings_surprises(a.pair)
    assert result[0]['percent_surprise'] == pytest.approx(20)
    result[0]['percent_surprise'] = -999
    assert p.get_earnings_surprises(a.pair)[0]['percent_surprise'] == pytest.approx(20)


def test_provider_rejects_duplicate_revisions_unknown_pair_and_naive_clock():
    a, f = observations()
    with pytest.raises(EquityAdmissionError): provider(records=[(a, f), (a, f)])
    with pytest.raises(EquityAdmissionError): provider(records=[(replace(a, pair='DEF/USD'), f)])
    p = provider()
    with pytest.raises(EquityAdmissionError): p._set_dataframe_max_date(AT.replace(tzinfo=None))


def test_period_completion_uses_market_local_date_and_boolean_is_unknown():
    a, f = observations()
    early = datetime(2025, 1, 1, 0, 30, tzinfo=UTC)  # Still Dec31 in New York.
    a = replace(a, published_at=early, observed_at=early + timedelta(minutes=1))
    f = replace(f, published_at=early - timedelta(days=1), observed_at=early - timedelta(hours=1))
    assert consensus_surprise(a, f, at=early + timedelta(hours=1))['status'] == 'unknown'
    a, f = observations()
    assert consensus_surprise(replace(a, value=True), f, at=AT + timedelta(days=1))['status'] == 'unknown'
