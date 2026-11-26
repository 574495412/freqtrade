"""Reconcile native ZIP wallet balances and fee recognition from actual fills.

Stdlib Decimal and exported tables only. Native spot quote cash recognizes
remaining entry fees at realization; the economic NAV reserves them separately.
This audit explains that convention rather than treating raw cash as net NAV.
"""
import io
import json
from decimal import Decimal as D
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

HERE = Path(__file__).absolute().parent


def main():
    plan = json.loads((HERE / 'plan.json').read_text())
    reports = []
    for candidate in plan['candidates']:
        directory = Path(plan['output_root']) / (candidate['name'] + f"_{candidate['cost_bps']}bps")
        archive = next((directory / 'native_results').glob('*.zip'))
        with ZipFile(archive) as z:
            file = next(n for n in z.namelist() if n.endswith('.json') and not n.endswith('_config.json'))
            stats = json.loads(z.read(file))['strategy'][candidate['strategy']]
            captures = pd.read_feather(io.BytesIO(z.read(next(n for n in z.namelist() if n.endswith('_wallet.feather')))))
        dates = pd.date_range(plan['start'], plan['comparison_end'], freq='h')
        clocks = dates.as_unit('ms').asi8
        assert not captures.duplicated(['date', 'currency']).any()
        quote_rows = captures.loc[captures.currency.eq('USDT')]
        assert pd.DatetimeIndex(quote_rows.date).as_unit('ns').equals(dates.as_unit('ns'))
        assert quote_rows.rate.eq(1.).all() and captures.date.isin(dates).all()
        assert np.max(np.abs(captures.total_quote - captures.balance * captures.rate)) < 1e-8
        for coin in ['BTC', 'ETH']:
            base_rows = captures.loc[captures.currency.eq(coin)]
            prices = pd.read_feather(Path(plan['datadir']) / (coin + '_USDT-1h.feather')).set_index('date').open
            np.testing.assert_array_equal(base_rows.rate, prices.reindex(pd.DatetimeIndex(base_rows.date)))
        native = captures.pivot(index='date', columns='currency', values='balance').reindex(dates)
        assert native.USDT.notna().all()
        # Native omits zero base balances during flat calendar intervals.
        # Every omission is validated below from actual fills,never from flags.
        quote = np.full(len(clocks), float(candidate['capital']))
        economic_cash = quote.copy()
        remaining_fee = np.zeros(len(clocks))
        quantities = {}
        for trade in stats['trades']:
            times, amounts, stakes, realized, flows = [], [D(0)], [D(0)], [D(0)], [D(0)]
            quantity, stake, profit, cashflow = D(0), D(0), D(0), D(0)
            fee_open, fee_close = D(str(trade['fee_open'])), D(str(trade['fee_close']))
            for order in trade['orders']:
                amount, price = D(str(order['amount'])), D(str(order['safe_price']))
                if order['ft_is_entry']:
                    quantity += amount
                    stake += amount * price
                    cashflow -= amount * price * (1 + fee_open)
                else:
                    average = stake / quantity
                    piece = amount * (price * (1 - fee_close) - average * (1 + fee_open))
                    profit += piece.quantize(D('0.00000001'))
                    stake -= average * amount
                    quantity -= amount
                    cashflow += amount * price * (1 - fee_close)
                times.append(order['order_filled_timestamp'])
                amounts.append(quantity)
                stakes.append(stake)
                realized.append(profit)
                flows.append(cashflow)
            before = np.searchsorted(np.array(times, dtype=np.int64), clocks, side='left')
            q = np.array([float(v) for v in amounts])[before]
            basis = np.array([float(v) for v in stakes])[before]
            pnl = np.array([float(v) for v in realized])[before]
            flow = np.array([float(v) for v in flows])[before]
            coin = trade['pair'].split('/')[0]
            quantities[coin] = quantities.get(coin, np.zeros(len(clocks))) + q
            quote += pnl - basis
            economic_cash += flow
            remaining_fee += basis * float(fee_open)
        errors = [float(np.max(np.abs(native.USDT.to_numpy() - quote)))]
        for coin, expected in quantities.items():
            missing = native[coin].isna().to_numpy()
            assert np.all(expected[missing] == 0), (candidate['name'], coin, 'missing held balance')
            assert np.all(expected[~missing] > 0)
            errors.append(float(np.max(np.abs(native[coin].fillna(0).to_numpy() - expected))))
        # Accumulated native realized-profit eight-decimal rounding is visible
        # here; reserve recognition is not hidden as a fabricated fee add-back.
        cash_error = float(np.max(np.abs(native.USDT.to_numpy() - remaining_fee - economic_cash)))
        assert max(errors) < 1e-6 and cash_error < 1e-6
        assert economic_cash.min() > 0
        assert len(captures) == len(dates) + sum(int(np.count_nonzero(q)) for q in quantities.values())
        reports.append(dict(account=candidate['name'], actual_wallet_rows=len(captures),
                             actual_wallet_clocks=len(dates), maximum_native_balance_error=max(errors),
                             maximum_entry_fee_reserve_identity_error_usdt=cash_error,
                             minimum_independently_reconstructed_economic_cash=float(economic_cash.min()),
                             omitted_zero_base_balances_proven_from_fills=int(sum(native[c].isna().sum() for c in quantities)), passed=True))
    result = dict(passed=True, source='native ZIP _wallet.feather and actual orders',
                  actual_native_balance_rows=sum(r['actual_wallet_rows'] for r in reports),
                  complete_target_quote_cash_clocks=sum(r['actual_wallet_clocks'] for r in reports),
                  quote_convention='native quote cash minus remaining entry fee reserve equals economic cash,within realized-PnL rounding',
                  private_exchange_cash_certified=False, candidate_calculator_imported=False, accounts=reports)
    (HERE / 'native_wallet_audit.json').write_text(json.dumps(result, indent=2) + '\n')
    print('PASS', result['actual_native_balance_rows'], 'native wallet rows;actualcash/holdings/entry-fee recognition audited')


if __name__ == '__main__':
    main()
