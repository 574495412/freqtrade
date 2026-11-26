"""Result admission and invariant checks using previously audited native paths."""
import json
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from bootstrap import SOURCE, boot

boot()
from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.equity_backtesting import EquityBacktesting
from freqtrade.optimize.equity_nav import EquityMark, EquityNavSnapshot
from freqtrade.optimize.equity_net_result import EquityFixtureNetResultMixin, synthetic_net_result


def typed(raw):
    row = raw.copy()
    row['at'] = datetime.fromisoformat(row['at'])
    for key in ['execution_fees', 'closed_trade_net_cash_profit', 'dividend_receivable', 'paid_dividends']:
        row[key] = Decimal(row[key])
    row['holdings'] = tuple((pair, q, EquityMark(**dict(mark, at=datetime.fromisoformat(mark['at']))))
                            for pair, q, mark in row['holdings'])
    row['trade_fees'] = tuple((a, b, Decimal(c), d) for a, b, c, d in row['trade_fees'])
    return EquityNavSnapshot(**row)


def fixture(market='XNYS', case='base'):
    raw = json.loads((SOURCE/'runs'/(market+'_'+case)/'native_trace.json').read_text())
    opens, closes = [list(map(typed, raw[k])) for k in ['opens', 'closes']]
    return opens, closes, dict(expected_opens=[s.at for s in opens], expected_closes=[s.at for s in closes])


@pytest.mark.parametrize('market,case', [(m,c) for m in ['XNYS','XHKG'] for c in ['base','extrema','held']])
def test_actual_native_boundary_result_excludes_gross_trade_statistics(market, case):
    opens, closes, clocks = fixture(market, case)
    result = synthetic_net_result(opens, closes, **clocks)
    values = [Decimal(str(s.net_nav)) for p in zip(opens, closes) for s in p]
    peak, drawdown = values[0], Decimal(0)
    for value in values:
        peak = max(peak, value)
        drawdown = max(drawdown, 1-value/peak)
    assert abs(result['synthetic_sampled_max_drawdown']-float(drawdown)) < 1e-12
    assert abs(result['synthetic_net_change_ratio']-float(values[-1]/values[0]-1)) < 1e-12
    assert result['execution_fees'] == str(closes[-1].execution_fees)
    assert result['financial_strategy_performance'] is result['cagr'] is result['sharpe'] is None
    assert result['historical_equity_accounts'] == result['bench_records'] == 0
    assert bool(result['terminal_holdings']) == (case == 'held')


@pytest.mark.parametrize('fault', ['missing','duplicate_clock','wrong_expected','wrong_currency',
    'fee_disappeared','dividend_disappeared','cash_partition','holdings_value','nav_double_count',
    'initial_not_flat','boolean_amount','nonfinite','negative_fills','naive_clock','wrong_mark'])
def test_reject_incomplete_or_incoherent_boundary(fault):
    opens, closes, clocks = fixture(case='held')
    if fault == 'missing': closes.pop()
    if fault == 'duplicate_clock': closes[0] = replace(closes[0], at=opens[0].at)
    if fault == 'wrong_expected': clocks['expected_closes'][-1] += timedelta(days=1)
    if fault == 'wrong_currency': closes[-1] = replace(closes[-1], currency='HKD')
    if fault == 'fee_disappeared': closes[-1] = replace(closes[-1], execution_fees=Decimal(0))
    if fault == 'dividend_disappeared': closes[-1] = replace(closes[-1], paid_dividends=Decimal(-1))
    if fault == 'cash_partition': closes[-1] = replace(closes[-1], free_cash=closes[-1].free_cash+1)
    if fault == 'holdings_value': closes[-1] = replace(closes[-1], market_value=closes[-1].market_value+1)
    if fault == 'nav_double_count': closes[-1] = replace(closes[-1], net_nav=closes[-1].net_nav+1)
    if fault == 'initial_not_flat': opens[0] = replace(opens[0], filled_orders=1)
    if fault == 'boolean_amount': closes[-1] = replace(closes[-1], free_cash=True)
    if fault == 'nonfinite': closes[-1] = replace(closes[-1], net_nav=float('nan'))
    if fault == 'negative_fills': closes[-1] = replace(closes[-1], filled_orders=-1)
    if fault == 'naive_clock': closes[-1] = replace(closes[-1], at=closes[-1].at.replace(tzinfo=None))
    if fault == 'wrong_mark':
        p,q,m = closes[-1].holdings[0]
        closes[-1] = replace(closes[-1], holdings=((p,q,replace(m,at=m.at+timedelta(days=1))),))
    with pytest.raises(EquityAdmissionError): synthetic_net_result(opens, closes, **clocks)


def test_historical_constructor_still_closed_before_native_initialization():
    with patch.object(Backtesting, '__init__') as constructor:
        with pytest.raises(EquityAdmissionError): EquityBacktesting({})
        constructor.assert_not_called()


def test_unpaid_dividend_claim_changes_nav_without_funding_free_cash():
    opens, closes, clocks = fixture(case='held')
    original = closes[-1]
    closes[-1] = replace(original, dividend_receivable=Decimal(10), net_nav=original.net_nav+10)
    result = synthetic_net_result(opens, closes, **clocks)
    assert closes[-1].free_cash == original.free_cash
    assert result['terminal_dividend_receivable'] == '10'
    assert result['final_net_nav'] == original.net_nav+10


def test_dividend_payment_moves_claim_to_cash_without_creating_second_income():
    opens, closes, clocks = fixture(case='held')
    first, last = opens[-1], closes[-1]
    opens[-1] = replace(first, dividend_receivable=Decimal(10), net_nav=first.net_nav+10)
    closes[-1] = replace(last, paid_dividends=Decimal(10), total_cash=last.total_cash+10,
                         free_cash=last.free_cash+10, net_nav=last.net_nav+10)
    result = synthetic_net_result(opens, closes, **clocks)
    assert result['terminal_dividend_receivable'] == '0'
    assert result['paid_dividends'] == '10'
    assert result['final_net_nav'] == last.net_nav+10


def test_disappearing_unpaid_dividend_claim_cannot_become_completed_result():
    opens, closes, clocks = fixture(case='held')
    original = opens[-1]
    opens[-1] = replace(original, dividend_receivable=Decimal(10), net_nav=original.net_nav+10)
    with pytest.raises(EquityAdmissionError, match='disappeared'):
        synthetic_net_result(opens, closes, **clocks)


def test_report_mixin_does_not_replace_native_orders_or_account_engine():
    assert all(n not in EquityFixtureNetResultMixin.__dict__ for n in [
        '__init__', 'backtest_loop', '_enter_trade', '_try_close_open_order', '_process_exit_order',
        'time_pair_generator', '_time_pair_generator_det', 'load_bt_data', 'handle_left_open'])
