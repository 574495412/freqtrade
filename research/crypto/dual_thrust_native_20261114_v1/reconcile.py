"""Independent reconstruction from actual fills; generalized leverage guard only.

Original ledger arithmetic is retained; public native model validation does
not certify independent exchange settlement. Legacy admission flags remain.
"""
import math
import numpy as np
import pandas as pd
from research.crypto.tsmom_risk_native_20261114_v1.analyze import require

def ledger(stats, frame, capital, data, expected_leverage=1.0):
    times = pd.DatetimeIndex(frame.date).as_unit('ms').asi8
    total = np.full(len(times), float(capital))
    counts = np.zeros(len(times), dtype=int)
    gross = np.zeros(len(times))
    signed = np.zeros(len(times))
    ownership = {p: np.zeros(len(x['times']), dtype=int) for p, x in data.items()}
    fund_errors, order_count, multi = ([], 0, 0)
    unknown_positions = []
    gap = pd.Timestamp('2026-06-24T04:00Z').value // 10 ** 6
    for trade in stats['trades']:
        require(trade['leverage'] == expected_leverage, 'Unregistered native leverage')
        pair = trade['pair']
        orders = trade['orders']
        order_count += len(orders)
        multi += len(orders) > 2
        filled = np.array([o['order_filled_timestamp'] for o in orders], dtype=np.int64)
        require(np.all(filled[1:] >= filled[:-1]), 'Unordered native fills')
        qty = np.array([o['amount'] if o['ft_is_entry'] else -o['amount'] for o in orders])
        cash = np.array([o['amount'] * o['safe_price'] for o in orders])
        entry_cash = np.cumsum([v if o['ft_is_entry'] else 0.0 for v, o in zip(cash, orders, strict=True)])
        exit_cash = np.cumsum([v if not o['ft_is_entry'] else 0.0 for v, o in zip(cash, orders, strict=True)])
        quantities = np.cumsum(qty)
        require(abs(quantities[-1]) < 1e-08, 'Terminal quantity imbalance')
        before = np.searchsorted(filled, times, side='left')
        held = np.concatenate([[0.0], quantities])[before]
        held[np.abs(held) < 1e-10] = 0.0
        require(held.min() >= -1e-08, 'Negative reconstructed unsigned inventory')
        bought = np.concatenate([[0.0], entry_cash])[before]
        sold = np.concatenate([[0.0], exit_cash])[before]
        event_times = data[pair]['times']
        active_events = (event_times >= filled[0]) & (event_times <= filled[-1])
        at_event = np.concatenate([[0.0], quantities])[np.searchsorted(filled, event_times, side='left')]
        at_event = np.where(active_events, at_event, 0.0)
        at_event[np.abs(at_event) < 1e-10] = 0.0
        sign = -1 if trade['is_short'] else 1
        fees = -sign * at_event * data[pair]['unit']
        expected_funding = math.fsum(fees.tolist())
        fund_errors.append(abs(expected_funding - trade['funding_fees']))
        ownership[pair] += (at_event > 0).astype(int)
        accrued = np.concatenate([[0.0], np.cumsum(fees)])[np.searchsorted(event_times, times, side='left')]
        prices = data[pair]['marks']
        total += sign * (sold + held * prices - bought) - bought * trade['fee_open'] - sold * trade['fee_close'] - held * prices * trade['fee_close'] + accrued
        counts += (held > 0).astype(int)
        gross += held * prices
        signed += sign * held * prices
        if pair == 'TIA/USDT:USDT':
            gap_qty = np.concatenate([[0.0], quantities])[np.searchsorted(filled, gap, side='left')]
            if gap_qty > 1e-10:
                unknown_positions.append(dict(pair=pair, trade_id=trade.get('trade_id'), side='short' if trade['is_short'] else 'long', quantity=float(gap_qty), event='2026-06-24T04:00Z', unknown_funding_rate=None))
    error = float(np.max(np.abs(total - frame.hourly_mark_equity.to_numpy())))
    require(error < 3e-05, f'Actual fill/mark/funding NAV mismatch {error}')
    require(np.array_equal(counts, frame.open_trades.to_numpy()), 'Actual open position counts differ')
    require(max(fund_errors, default=0.0) < 1e-07, 'Quantity-aware funding differs from native')
    duplicates = sum((int((v > 1).sum()) for v in ownership.values()))
    require(duplicates == 0, 'Funding event assigned to multiple same-pair lifecycles')
    return dict(passed=True, hourly_points=len(times), maximum_nav_error_usdt=error, maximum_funding_error_usdt=max(fund_errors, default=0.0), filled_orders=order_count, multi_order_trades=multi, supplied_events=sum((len(x['times']) for x in data.values())), assigned_event_lifecycles=sum((int(v.sum()) for v in ownership.values())), dual_owned_events=duplicates, maximum_gross_mark_exposure_to_nav=float(np.max(gross / frame.hourly_mark_equity)), maximum_abs_net_mark_exposure_to_nav=float(np.max(np.abs(signed / frame.hourly_mark_equity))), tia_missing_event_actual_exposure=unknown_positions, actual_exchange_settlement_certified=False, known_missing_tia_event_unexposed=not bool(unknown_positions), complete_economic_account_admitted=False)
