"""Independent Decimal order, settlement and NAV tie-out; no engine imports."""
import hashlib
import json
from copy import deepcopy
from bisect import bisect_right
from datetime import datetime
from decimal import Decimal as D, ROUND_HALF_UP, ROUND_UP
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent


def fee_for(order, contract):
    quantity, price = D(str(order['quantity'])), D(str(order['price']))
    total = D(0)
    for rule in contract['rules']:
        if rule['side'] != order['side']:
            continue
        assert rule['clock'] == 'trade_date'
        value = max(D(rule['minimum']), quantity * price * D(rule['rate']) + quantity * D(rule['per_share']) + D(rule['fixed']))
        if rule['maximum'] is not None:
            value = min(value, D(rule['maximum']))
        unit = D(rule['quantum'])
        total += (value / unit).to_integral_value(rounding=ROUND_UP if rule['rounding'] == 'UP' else ROUND_HALF_UP) * unit
    return total


def audit(name):
    directory = HERE / 'runs' / name
    record = json.loads((directory / 'native_trace.json').read_text())
    frozen = json.loads((directory / 'implementation_freeze.json').read_text())
    for p in [directory / 'synthetic_ohlcv.feather', directory / 'config.json']:
        assert hashlib.sha256(p.read_bytes()).hexdigest() == frozen[str(p.resolve())]
    assert record['native_constructor'] and record['native_backtest'] and record['native_start']
    assert record['historical_accounts'] == 0
    prices = pd.read_feather(directory / 'synthetic_ohlcv.feather').set_index('date')
    cash_contract = record['cash_contract']
    sessions, settlement = cash_contract['sessions'], cash_contract['settlement_days']
    opens = [datetime.fromisoformat(s['opens']) for s in sessions]
    assert len(record['opens']) == len(record['closes']) == len(opens) == 7
    assert len(record['callbacks']) == len(opens)
    labels = [s['label'] for s in settlement]
    orders = record['orders']
    fee_contract = record['fee_contract']
    for order in orders:
        assert datetime.fromisoformat(order['at']) in opens
        assert order['quantity'] > 0 and order['quantity'] % 1 == 0
        label = pd.Timestamp(order['at']).normalize()
        assert order['price'] == prices.loc[label, 'open']
    checks = 0
    for kind, snapshots in [('open', record['opens']), ('close', record['closes'])]:
        for i, snapshot in enumerate(snapshots):
            at = datetime.fromisoformat(snapshot['at'])
            assert at == datetime.fromisoformat(sessions[i]['opens' if kind == 'open' else 'closes'])
            cash, shares, fees, locked = D(1000), D(0), D(0), D(0)
            count = 0
            for order in orders:
                fill = datetime.fromisoformat(order['at'])
                if fill > at or (kind == 'open' and fill == at):
                    continue
                count += 1
                quantity, price = D(str(order['quantity'])), D(str(order['price']))
                charge = fee_for(order, fee_contract)
                fees += charge
                if order['side'] == 'buy':
                    shares += quantity
                    cash -= quantity * price + charge
                else:
                    shares -= quantity
                    proceeds = quantity * price - charge
                    cash += proceeds
                    lag = 1 if record['market'] == 'XNYS' else 2
                    release = settlement[bisect_right(labels, fill.date().isoformat()) + lag - 1]
                    if datetime.fromisoformat(release['available_at']) > at:
                        locked += proceeds
            assert shares >= 0
            mark = D(str(prices.loc[pd.Timestamp(at).normalize(), kind]))
            expected = {'total_cash': cash, 'free_cash': cash - locked,
                        'unsettled_receivable': locked, 'market_value': shares * mark,
                        'net_nav': cash + shares * mark, 'execution_fees': fees}
            for key, value in expected.items():
                assert abs(D(str(snapshot[key])) - value) < D('0.00000001'), (name, i, kind, key)
                checks += 1
            assert snapshot['filled_orders'] == count
            assert snapshot['dividend_receivable'] == '0' and snapshot['paid_dividends'] == '0'
            assert len(snapshot['holdings']) == int(shares != 0)
            if shares:
                assert D(str(snapshot['holdings'][0][1])) == shares
                assert D(str(snapshot['holdings'][0][2]['price'])) == mark
            callback = record['callbacks'][i]
            assert datetime.fromisoformat(callback['known']) < datetime.fromisoformat(callback['at']) == opens[i]
    return dict(name=name, market=record['market'], checked_scalars=checks,
                fills=len(orders), snapshots=14, terminal_held=record['terminal_held_fixture'],
                historical_accounts=0, passed=True)


def main():
    names = ['XNYS_base', 'XHKG_base', 'XNYS_extrema', 'XHKG_extrema', 'XNYS_held', 'XHKG_held']
    rows = [audit(n) for n in names]
    for market, previous in [('XNYS', 'us'), ('XHKG', 'hk')]:
        base = json.loads((HERE / 'runs' / (market + '_base') / 'native_trace.json').read_text())
        altered = json.loads((HERE / 'runs' / (market + '_extrema') / 'native_trace.json').read_text())
        for records in [(base, altered)]:
            for key in ['orders', 'opens', 'closes', 'callbacks']:
                left, right = deepcopy(records[0][key]), deepcopy(records[1][key])
                if key in ['opens', 'closes']:
                    for trace in [left, right]:
                        for snapshot in trace:
                            for holding in snapshot['holdings']: holding[2].pop('source_sha256')
                assert left == right, (market, key)
        for variant, old_suffix in [('base', '_final'), ('extrema', '_extrema_final'), ('held', '_held_final')]:
            directory = HERE / 'runs' / (market + '_' + variant)
            new = json.loads((directory / 'native_trace.json').read_text())
            old = json.loads((HERE.parent / 'open_only_native_20261115_v1' / 'runs' / (previous + old_suffix) / 'native_trace.json').read_text())
            for key in ['orders', 'opens', 'closes', 'callbacks']:
                left, right = deepcopy(new[key]), deepcopy(old[key])
                if key in ['opens', 'closes']:
                    for trace in [left, right]:
                        for snapshot in trace:
                            for holding in snapshot['holdings']: holding[2].pop('source_sha256')
                assert left == right, (market, variant, key)
            result = json.loads((directory / 'equity_fixture_result.json').read_text())
            last = new['closes'][-1]
            assert result['final_net_nav'] == last['net_nav']
            assert result['execution_fees'] == last['execution_fees']
            assert result['financial_strategy_performance'] is None and result['bench_records'] == 0
            assert result['terminal_holdings'] == last['holdings']
            assert datetime.fromisoformat(result['start']) == datetime.fromisoformat(new['opens'][0]['at'])
            assert datetime.fromisoformat(result['end']) == datetime.fromisoformat(last['at'])
            exports = json.loads((directory / 'native_export_receipt.json').read_text())
            assert len(exports) == 1
            for receipt in exports:
                assert hashlib.sha256(Path(receipt['path']).read_bytes()).hexdigest() == receipt['sha256']
    out = dict(passed=True, scenarios=rows, scalar_checks=sum(x['checked_scalars'] for x in rows),
               native_synthetic_fills=sum(x['fills'] for x in rows),
               exact_future_daily_extrema_invariance=['XNYS', 'XHKG'],
               exact_prior_backtest_financial_paths=6, native_start_lifecycles=6, native_zip_exports=6,
               historical_accounts=0, strategy_performance=None)
    (HERE / 'trace_audit.json').write_text(json.dumps(out, indent=2) + '\n')
    print('PASSED independent native order/cash/NAV audit;', out['scalar_checks'], 'scalars;', out['native_synthetic_fills'], 'synthetic fills; 0 historical accounts')


if __name__ == '__main__':
    main()
