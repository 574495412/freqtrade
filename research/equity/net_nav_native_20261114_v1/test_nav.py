"""Net reporting checks using real native synthetic Trade/Order fills."""
import sys
from dataclasses import asdict, replace
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent / 'dated_fees_native_20261114_v1'))
from test_fees import at, contract, fill, probe, zero_trade
from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.optimize.equity_nav import EquityMark, EquityNavSeries, capture_equity_nav
from freqtrade.persistence import LocalTrade, Trade


@pytest.fixture(autouse=True)
def isolated():
    old = Trade.use_db
    Trade.use_db = False
    LocalTrade.reset_trades()
    with patch.object(Trade, 'commit'):
        yield
    LocalTrade.reset_trades()
    Trade.use_db = old


def mark(pair='ABC/USD', when=None, price=100, **changes):
    return replace(EquityMark(pair, pair.split('/')[-1], when or at(2), price, 'close', 'd'*64), **changes)


def snap(a, price=100):
    w = a.wallets
    pair = 'ABC/' + w.contract.currency
    quotes = [mark(pair, w.asof, price)] if w.get_owned(pair, 'ABC') else []
    return capture_equity_nav(w, quotes, at=w.asof, mark_kind='close')


@pytest.mark.parametrize('market', ['XNYS', 'XHKG'])
def test_full_native_trace_against_independent_decimal(market):
    result = native_trace(market)
    assert result['passed']


def native_trace(market):
    c = contract(market)
    a = probe(c)
    w = a.wallets
    hour = 15 if market == 'XNYS' else 2
    sequence = [(at(2,hour),2,100,True), (at(3,hour),3,110,True),
                (at(6,hour),2,120,False), (at(7,hour),3,90,False)]
    clocks = (w.asof, *(r[0] for r in sequence), c.settlement_days[-1].available_at)
    series = EquityNavSeries(w, expected_clocks=clocks, mark_kind='close')
    series.capture([])
    t = zero_trade('ABC/' + c.currency, at(2,hour))
    cash, qty, fees, receivables, rows = D(1000), D(0), D(0), [], []
    for when, amount, price, buy in sequence:
        fill(a,t,when,amount,price,buy)
        gross = D(amount)*D(price)
        # Independent synthetic fee contract arithmetic, not production quote().
        fee = D(1) + (gross*D('.002') if not buy else D(0))
        fees += fee
        cash += (-gross if buy else gross) - fee
        qty += D(amount) if buy else -D(amount)
        if not buy:
            receivables.append((c.release_for_fill(when).available_at, gross-fee))
        locked = sum((v for release,v in receivables if release>when), D(0))
        s = series.capture([mark(t.pair,when,price)] if qty else [])
        for actual, expected in [(s.net_nav,cash+qty*D(price)),(s.total_cash,cash),
                                 (s.free_cash,cash-locked),(s.unsettled_receivable,locked)]:
            assert abs(D(str(actual))-expected) < D('.00000001')
        assert s.execution_fees == fees
        assert s.closed_trade_net_cash_profit == (cash-D(1000) if not qty else D(0))
        # Repeated reporting cannot charge fees twice or change native quantities.
        assert capture_equity_nav(w, [mark(t.pair,when,price)] if qty else [], at=when, mark_kind='close') == s
        rows.append(asdict(s))
    w.advance(clocks[-1])
    series.capture([])
    result = series.component_result()
    assert result['final_net_nav'] == pytest.approx(974.98)
    assert D(result['execution_fees']) == D('5.02')
    assert result['net_change_ratio'] == pytest.approx(-.02502)
    expected_navs = [1000,999,1018,1066.52,974.98,974.98]
    peak, dd = D(0), D(0)
    for value in map(lambda v:D(str(v)), expected_navs):
        peak=max(peak,value)
        dd=max(dd,1-value/peak)
    assert result['sampled_max_drawdown'] == pytest.approx(float(dd))
    assert result['historical_strategy_accounts'] == 0
    return {'market':market,'native_fills':4,'snapshots':6,'passed':True,
            'snapshots_after_fills':rows,'component_result':result,
            'all_snapshots':[asdict(s) for s in series.snapshots],
            'native_gross_profit':t.close_profit_abs,
            'closed_net_profit':str(series.snapshots[-1].closed_trade_net_cash_profit)}


@pytest.mark.parametrize('change', [
    {'price':0},{'price':-1},{'price':float('nan')},{'price':float('inf')},{'price':True},
    {'source_sha256':''},{'kind':'mid'},{'at':at(2).replace(tzinfo=None)}, {'currency':'HKD'},
])
def test_invalid_marks_reject(change):
    with pytest.raises(EquityAdmissionError):
        mark(**change)


@pytest.mark.parametrize('fault', ['missing','duplicate','stale','future','kind','extra','wrong_clock','foreign'])
def test_observation_contract_rejects(fault):
    a=probe();t=zero_trade();fill(a,t,at(2),2,100)
    m=mark();marks=[m];clock=at(2)
    if fault=='missing':marks=[]
    elif fault=='duplicate':marks=[m,m]
    elif fault=='stale':marks=[replace(m,at=at(2,14))]
    elif fault=='future':marks=[replace(m,at=at(3))]
    elif fault=='kind':marks=[replace(m,kind='open')]
    elif fault=='extra':marks.append(mark('OTHER/USD'))
    elif fault=='foreign':marks=[mark('ABC/HKD')]
    else:clock=at(3)
    with pytest.raises(EquityAdmissionError):
        capture_equity_nav(a.wallets,marks,at=clock,mark_kind='close')


@pytest.mark.parametrize('clocks', [(),(at(2),),(at(2),at(2)),(at(3),at(2)),(at(2).replace(tzinfo=None),at(3))])
def test_invalid_declared_grids_reject(clocks):
    with pytest.raises(EquityAdmissionError):
        EquityNavSeries(probe().wallets,expected_clocks=clocks,mark_kind='close')


def test_incomplete_duplicate_and_skipped_grid_have_no_result():
    w=probe().wallets
    s=EquityNavSeries(w,expected_clocks=[w.asof,at(3),at(6)],mark_kind='close')
    s.capture([])
    with pytest.raises(EquityAdmissionError):s.component_result()
    with pytest.raises(EquityAdmissionError):s.capture([])
    w.advance(at(6))
    with pytest.raises(EquityAdmissionError):s.capture([])
    assert len(s.snapshots)==1


def test_initial_invested_state_cannot_be_reset_as_initial_cash():
    a=probe();t=zero_trade();fill(a,t,at(2),2,100)
    s=EquityNavSeries(a.wallets,expected_clocks=[at(2),at(3)],mark_kind='close')
    with pytest.raises(EquityAdmissionError):s.capture([mark()])
    assert not s.snapshots


def test_missing_mark_failure_has_no_series_append_or_cash_change():
    a=probe();w=a.wallets
    s=EquityNavSeries(w,expected_clocks=[w.asof,at(2)],mark_kind='close');s.capture([])
    t=zero_trade();fill(a,t,at(2),2,100)
    before=(w.get_total('USD'),w.total_execution_fees,t.amount,t.realized_profit)
    with pytest.raises(EquityAdmissionError):s.capture([])
    assert before==(w.get_total('USD'),w.total_execution_fees,t.amount,t.realized_profit)
    assert len(s.snapshots)==1
    s.capture([mark()]);assert len(s.snapshots)==2


def test_open_terminal_is_marked_not_imaginary_liquidated():
    a=probe();w=a.wallets
    s=EquityNavSeries(w,expected_clocks=[w.asof,at(2)],mark_kind='close');s.capture([])
    t=zero_trade();fill(a,t,at(2),2,100);s.capture([mark(price=110)])
    result=s.component_result()
    assert result['final_net_nav']==1019
    assert result['terminal']=='observed holdings mark'
    assert D(result['execution_fees'])==D('1.00')
    assert t.is_open and t.amount==2


def test_closed_record_quantity_is_not_nav_holding():
    a=probe();t=zero_trade();fill(a,t,at(2),2,100);fill(a,t,at(3),2,100,False)
    assert not t.is_open and t.amount==2
    s=snap(a)
    assert not s.holdings and s.market_value==0 and s.net_nav==pytest.approx(997.6)
    assert s.closed_trade_net_cash_profit==D('-2.4')


def test_no_fee_double_deduction_and_cash_release_does_not_move_nav():
    a=probe();t=zero_trade();fill(a,t,at(2),2,100);fill(a,t,at(3),1,120,False)
    before=snap(a,120)
    assert before.net_nav==pytest.approx(1037.76)
    assert before.closed_trade_net_cash_profit==0
    a.wallets.advance(a.wallets.contract.release_for_fill(at(3)).available_at)
    after=snap(a,120)
    assert after.net_nav==before.net_nav
    assert after.execution_fees==before.execution_fees
    assert after.unsettled_receivable==0 and after.free_cash>before.free_cash


def test_missing_id_attribution_rejects():
    a=probe();t=zero_trade();fill(a,t,at(2),2,100);t.id=None
    with pytest.raises(EquityAdmissionError):snap(a)


@pytest.mark.parametrize('change',['wallet','fees','cash','capital'])
def test_no_contract_or_capital_switch_during_series(change):
    a=probe();w=a.wallets
    s=EquityNavSeries(w,expected_clocks=[w.asof,at(2)],mark_kind='close');s.capture([])
    w.advance(at(2))
    if change=='wallet':s.wallet=probe().wallets
    elif change=='fees':w.fees=replace(w.fees)
    elif change=='cash':w.contract=replace(w.contract)
    else:w._start_cap['USD']=500
    with pytest.raises(EquityAdmissionError):s.capture([])


def test_reset_native_trade_history_cannot_create_recovered_cash():
    a=probe();w=a.wallets
    s=EquityNavSeries(w,expected_clocks=[w.asof,at(2),at(3)],mark_kind='close');s.capture([])
    t=zero_trade();fill(a,t,at(2),2,100);s.capture([mark()])
    LocalTrade.reset_trades();w.advance(at(3))
    with pytest.raises(EquityAdmissionError):s.capture([])


def test_untouched_cash_series_and_missing_wallet_reject():
    a=probe();w=a.wallets
    s=EquityNavSeries(w,expected_clocks=[w.asof,at(3)],mark_kind='close');s.capture([])
    w.advance(at(3));s.capture([])
    result=s.component_result()
    assert result['net_change_ratio']==0 and result['sampled_max_drawdown']==0
    with pytest.raises(EquityAdmissionError):capture_equity_nav(None,[],at=at(3),mark_kind='close')
