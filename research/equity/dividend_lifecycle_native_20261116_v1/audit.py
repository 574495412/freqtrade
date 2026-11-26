"""Independent Decimal reconstruction from exported orders and raw fixtures.

No strategy, wallet, candidate accounting module or source event calculator is
imported. This is an audit of native evidence, never a primary return engine.
"""
import hashlib
import importlib.util
import json
from bisect import bisect_right
from datetime import datetime
from decimal import Decimal as D, ROUND_HALF_UP
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

HERE = Path(__file__).absolute().parent
PARENT = HERE.with_name('net_result_native_20261115_v1')


def read(p):
    return json.loads(Path(p).read_text())


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def dt(s):
    return datetime.fromisoformat(s)


def main():
    fee_path = HERE.with_name('start_lifecycle_native_20261115_v1') / 'audit_trace.py'
    spec = importlib.util.spec_from_file_location('independent_fee_oracle', fee_path)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    receipts = read(HERE / 'execution_receipt.json')
    assert len(receipts) == 12 and all(r['returncode'] == 0 for r in receipts)
    traces, rows, scalar_checks = {}, [], 0
    for receipt in receipts:
        name = receipt['case']
        directory = HERE / 'runs' / name
        record = read(directory / 'native_trace.json')
        traces[name] = record
        prices = pd.read_feather(directory / 'synthetic_ohlcv.feather').set_index('date')
        frozen = read(directory / 'implementation_freeze.json')
        for path in [directory / 'config.json', directory / 'synthetic_ohlcv.feather']:
            assert sha(path) == frozen[str(path)]
        assert record['native_constructor'] and record['native_backtest'] and record['native_start']
        assert record['historical_accounts'] == record['bench_records'] == 0
        orders = record['orders']
        assert len(orders) == 3
        sessions = record['cash_contract']['sessions']
        settlement = record['cash_contract']['settlement_days']
        labels = [r['label'] for r in settlement]
        lag = 1 if record['market'] == 'XNYS' else 2
        events = record['dividend_contract']['events']
        for kind in ['open', 'close']:
            for i, snapshot in enumerate(record[kind + 's']):
                at = dt(snapshot['at'])
                assert at == dt(sessions[i]['opens' if kind == 'open' else 'closes'])
                cash, shares, fees, locked = D(1000), D(0), D(0), D(0)
                paid, receivable, count = D(0), D(0), 0
                for order in orders:
                    filled = dt(order['at'])
                    if filled > at or (kind == 'open' and filled == at):
                        continue
                    count += 1
                    quantity, price = D(str(order['quantity'])), D(str(order['price']))
                    charge = old.fee_for(order, record['fee_contract'])
                    fees += charge
                    if order['side'] == 'buy':
                        cash -= quantity * price + charge
                        shares += quantity
                    else:
                        proceeds = quantity * price - charge
                        cash += proceeds
                        shares -= quantity
                        release = settlement[bisect_right(labels, filled.date().isoformat()) + lag - 1]
                        if dt(release['available_at']) > at:
                            locked += proceeds
                for event in events:
                    ex = dt(event['ex_open'])
                    if ex > at:
                        continue
                    entitled = sum((D(str(o['quantity'])) * (1 if o['side'] == 'buy' else -1)
                                    for o in orders if dt(o['at']) < ex), D(0))
                    assert entitled >= 0
                    gross = (entitled * D(event['gross_per_share'])).quantize(D('.01'), rounding=ROUND_HALF_UP)
                    tax = (gross * D(event['withholding_rate'])).quantize(D('.01'), rounding=ROUND_HALF_UP)
                    net = gross - tax
                    if (event['payment_known_at'] is not None
                            and dt(event['payment_known_at']) <= at
                            and dt(event['available_at']) <= at):
                        paid += net
                    else:
                        receivable += net
                cash += paid
                mark = D(str(prices.loc[pd.Timestamp(at).normalize(), kind]))
                expected = dict(total_cash=cash, free_cash=cash - locked, unsettled_receivable=locked,
                                market_value=shares * mark, net_nav=cash + shares * mark + receivable,
                                execution_fees=fees, paid_dividends=paid, dividend_receivable=receivable)
                for key, value in expected.items():
                    assert abs(D(str(snapshot[key])) - value) < D('1e-8'), (name, kind, i, key, snapshot[key], value)
                    scalar_checks += 1
                assert snapshot['filled_orders'] == count
                assert len(snapshot['holdings']) == int(shares > 0)
                if shares:
                    assert D(str(snapshot['holdings'][0][1])) == shares
                callback = record['callbacks'][i]
                assert dt(callback['known']) < dt(callback['at']) == dt(sessions[i]['opens'])
        exports = read(directory / 'native_export_receipt.json')
        assert len(exports) == 1
        assert sha(exports[0]['path']) == exports[0]['sha256']
        with ZipFile(exports[0]['path']) as archive:
            n = next(n for n in archive.namelist() if n.endswith('.json') and not n.endswith('_config.json'))
            stats = json.loads(archive.read(n))['strategy']['OpenClockProbe']
            assert stats['profit_total_abs'] == 0
            exported_orders = [o for t in stats['trades'] for o in t['orders']]
            closed_orders = [o for o in orders if not o['is_open']]
            assert len(exported_orders) == len(closed_orders) == 2
            for exported, actual in zip(exported_orders, closed_orders, strict=True):
                assert exported['amount'] == actual['quantity']
                assert exported['safe_price'] == actual['price']
                assert exported['ft_order_side'] == actual['side']
                assert exported['order_filled_timestamp'] == int(dt(actual['at']).timestamp() * 1000)
        journal = read(directory / 'dividend_journal.json')
        assert all(j['identity'] != 'synthetic-ex-open-buy-excluded' for j in journal)
        if record['dividend_case'] != 'none':
            first = next(j for j in journal if j['identity'] == 'synthetic-pre-ex-holding')
            assert D(first['quantity']) == 6 and D(first['net']) == 54
        result = read(directory / 'equity_net_result.json')
        snapshots = [s for pair in zip(record['opens'], record['closes'], strict=True) for s in pair]
        values = [D(str(s['net_nav'])) for s in snapshots]
        peak, dd = values[0], D(0)
        for value in values:
            peak = max(peak, value)
            dd = max(dd, 1 - value / peak)
        assert abs(D(str(result['synthetic_net_change_ratio'])) - (values[-1] / values[0] - 1)) < D('1e-12')
        assert abs(D(str(result['synthetic_sampled_max_drawdown'])) - dd) < D('1e-12')
        assert result['financial_strategy_performance'] is result['cagr'] is result['sharpe'] is None
        assert result['historical_equity_accounts'] == result['bench_records'] == 0
        assert len(result['terminal_holdings']) == 1
        rows.append(dict(case=name, fills=3, snapshots=14, final_fixture_nav=values[-1].to_eng_string(),
                         paid=result['paid_dividends'], unpaid=result['terminal_dividend_receivable'], passed=True))
    counterfactuals = []
    for market in ['XNYS', 'XHKG']:
        none = traces[market + '_none']
        previous = read(PARENT / 'runs' / (market + '_held_main') / 'native_trace.json')
        for key in ['orders', 'opens', 'closes', 'callbacks']:
            assert none[key] == previous[key], (market, 'parent_default', key)
        assert read(HERE / 'runs' / (market + '_none') / 'equity_net_result.json') == read(
            PARENT / 'runs' / (market + '_held_main') / 'equity_net_result.json')
        for left, right, cutoff_index in [('paid', 'pending', 2), ('delayed', 'pending', 5),
                                          ('future1', 'future999', 6), ('future1', 'paid', 6)]:
            a, b = traces[market + '_' + left], traces[market + '_' + right]
            cutoff = dt(a['cash_contract']['sessions'][cutoff_index]['opens' if cutoff_index == 6 else 'closes'])
            assert a['orders'] == b['orders']
            compared = 0
            for key in ['opens', 'closes']:
                aa = [s for s in a[key] if dt(s['at']) < cutoff]
                bb = [s for s in b[key] if dt(s['at']) < cutoff]
                assert aa == bb, (market, left, right, key)
                compared += len(aa)
            counterfactuals.append(dict(market=market, left=left, right=right, before=cutoff.isoformat(),
                                        equal_financial_snapshots=compared))
        pending = traces[market + '_pending']['closes'][-1]
        paid = traces[market + '_paid']['closes'][-1]
        assert D(pending['dividend_receivable']) == D(paid['paid_dividends']) == 54
        assert D(pending['paid_dividends']) == D(paid['dividend_receivable']) == 0
        assert abs(D(str(paid['free_cash'])) - D(str(pending['free_cash'])) - 54) < D('1e-8')
        assert paid['net_nav'] == pending['net_nav']
    result = dict(passed=True, actual_native_starts=12, actual_native_zip=12, synthetic_fills=36,
                  actual_boundaries=168, independent_scalar_checks=scalar_checks,
                  default_parent_paths_exact=2, counterfactuals=counterfactuals,
                  independent_fee_oracle=dict(path=str(fee_path), sha256=sha(fee_path)),
                  candidate_accounting_imported=False, cases=rows,
                  native_zip_scope='one closed trade/two fills; remaining actual native open Trade/Order retained in trace sidecar',
                  historical_accounts=0, factor_analysis=0, bench=0, financial_performance=None,
                  broker_credit_certified=False, production_eligible=False)
    (HERE / 'independent_audit.json').write_text(json.dumps(result, indent=2) + '\n')
    print('PASS 12 native starts/ZIPs,36 synthetic fills,168 boundaries,', scalar_checks, 'cash/dividend/NAV scalars; 0 historical accounts')


if __name__ == '__main__':
    main()
