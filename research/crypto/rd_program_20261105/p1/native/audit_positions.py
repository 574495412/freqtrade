"""Reconstruct filled positions from saved native orders, never run a strategy."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal as D
from statistics import mean, median

import numpy as np
import pandas as pd


def reconstruct(native_trades, index, prices, expected_gross, expected_terminal_equity, cost):
    """Match the observer's pre-event clock and exclude artificial terminal exits.

    Native terminal funding is separately labelled. Matching the aggregate NAV
    does not authenticate funding chronology, real settlement or execution.
    """
    if not len(index) or not index.is_unique or not index.is_monotonic_increasing:
        raise ValueError('A complete ordered observation index is required')
    timestamps = index.as_unit('ns').asi8
    long_gross = np.zeros(len(index)); short_gross = np.zeros(len(index))
    pairs = defaultdict(lambda: {'pnl': D(0), 'funding': D(0), 'filled_notional': D(0),
                                'fees': D(0), 'exit_reserve': D(0), 'terminal_notional': D(0)})
    campaigns = []
    total_orders = dca_orders = 0
    open_positions = []
    fee_rate = D(str(cost['fee_bps_per_side'])) / 10000
    proxy_rate = D(str(cost['execution_proxy_bps_per_side'])) / 10000
    maximum_inventory_residual = D(0)
    for trade in native_trades:
        pair = trade['pair']
        if pair not in prices:
            raise ValueError(f'Missing prices for {pair}')
        price = prices[pair].reindex(index).to_numpy(dtype=float)
        if not np.isfinite(price).all() or (price <= 0).any():
            raise ValueError('Nonfinite/missing reference prices')
        changes = np.zeros(len(index))
        quantity, cash, notional, fees = D(0), D(0), D(0), D(0)
        entries = []
        exits = []
        last_quantity = D(0)
        direction = D(-1 if trade['is_short'] else 1)
        orders = trade['orders']
        forced = trade['exit_reason'] == 'force_exit'
        if forced and (not orders or orders[-1]['ft_is_entry']):
            raise ValueError('Unexpected forced exit export')
        for ordinal, order in enumerate(orders):
            if forced and ordinal == len(orders)-1:
                # A 4h strategy may backdate native terminal closing to 20:00.
                # This artificial order must not erase inventory at 23:55.
                continue
            time = pd.Timestamp(order['order_filled_timestamp'], unit='ms', tz='UTC')
            if time < index[0]:
                raise ValueError('Unexpected position predating the registered flat account')
            insertion = int(np.searchsorted(timestamps, time.value, side='right'))
            if insertion == len(index):
                continue  # Event at endpoint is after its pre-event observation.
            amount = D(str(order['amount'])); value = amount * D(str(order['safe_price']))
            if amount <= 0 or value <= 0:
                raise ValueError('Invalid exported fill')
            entry = order['ft_is_entry']
            delta = amount if entry else -amount
            quantity += delta
            if quantity < D('-0.00000001'):
                raise ValueError('Exit quantity exceeds entries')
            changes[insertion] += float(delta)
            cash += direction * (-value if entry else value)
            fee = value * D(str(trade['fee_open'] if entry else trade['fee_close']))
            fees += fee; cash -= fee; notional += value; total_orders += 1
            (entries if entry else exits).append(time)
            last_quantity = quantity
        if not entries:
            continue
        dca_orders += max(0, len(entries)-1)
        inventory = np.cumsum(changes)
        maximum_inventory_residual = max(maximum_inventory_residual,abs(D(str(inventory[-1]))-last_quantity))
        inventory[np.abs(inventory) < 1e-8] = 0
        if (inventory < -1e-8).any():
            raise ValueError('Negative reconstructed position')
        exposure = inventory * price
        if trade['is_short']: short_gross += exposure
        else: long_gross += exposure
        remaining = quantity if abs(quantity) >= D('0.00000001') else D(0)
        terminal_notional = remaining * D(str(price[-1]))
        reserve = terminal_notional * D(str(trade['fee_close']))
        funding = D(str(trade['funding_fees'] or 0))
        # This is terminal native-model attribution. A chronology check below
        # refuses to call it complete when export closes after the observation.
        fully_closed_before_endpoint = remaining == 0 and bool(exits) and exits[-1] < index[-1]
        chronology_known = fully_closed_before_endpoint or forced
        pnl = cash + direction * terminal_notional - reserve + funding
        for key,value in [('pnl',pnl),('funding',funding),('filled_notional',notional),
                          ('fees',fees),('exit_reserve',reserve),('terminal_notional',terminal_notional)]:
            pairs[pair][key] += value
        end_time = exits[-1] if fully_closed_before_endpoint else index[-1]
        age = (end_time-entries[0]).total_seconds()/3600
        record = {'pair':pair,'direction':'short' if trade['is_short'] else 'long',
                  'entry_at':entries[0].isoformat(),'age_hours':age,'entry_fills':len(entries),
                  'exit_fills_before_endpoint':len(exits),'DCA_entry_fills':max(0,len(entries)-1),
                  'naturally_closed_before_endpoint':bool(fully_closed_before_endpoint and not forced),
                  'native_terminal_funding_chronology_classifiable':bool(chronology_known),
                  'remaining_quantity':float(remaining),'terminal_notional_usdt':float(terminal_notional),
                  'native_funding_total_usdt':float(funding),'reference_campaign_contribution_usdt':float(pnl)}
        campaigns.append(record)
        if remaining: open_positions.append(record)
    reconstructed_gross = long_gross + short_gross
    gross_error = float(np.max(np.abs(reconstructed_gross - np.asarray(expected_gross))))
    if gross_error > .01:
        raise ValueError(f'Filled-order gross differs from observer by {gross_error} USDT')
    total = sum((p['pnl'] for p in pairs.values()),D(0))
    nav_error = abs(D(1000)+total-D(str(expected_terminal_equity)))
    funding_coverage = all(c['native_terminal_funding_chronology_classifiable'] for c in campaigns)
    contribution_available = nav_error <= D('.01') and funding_coverage
    positive_pair = sum((max(D(0),p['pnl']) for p in pairs.values()),D(0))
    positive_campaign = sum(max(0,c['reference_campaign_contribution_usdt']) for c in campaigns)
    total_notional = sum((p['filled_notional'] for p in pairs.values()),D(0))
    total_reserve = sum((p['exit_reserve'] for p in pairs.values()),D(0))
    gross = long_gross + short_gross; net = long_gross - short_gross
    rows = []
    for pair,values in sorted(pairs.items()):
        rows.append({'pair':pair, **{k:float(v) for k,v in values.items()},
                     'positive_contribution_share':float(max(D(0),values['pnl'])/positive_pair) if contribution_available and positive_pair else None})
    completed_ages = [c['age_hours'] for c in campaigns if c['naturally_closed_before_endpoint']]
    open_ages = [c['age_hours'] for c in open_positions]
    return {
        'scope':'read-only filled-order reconstruction; same conditional OPEN valuation as observer',
        'gross_maximum_error_usdt':gross_error,'terminal_nav_reconstruction_error_usdt':float(nav_error),
        'maximum_float_quantity_accumulation_residual':str(maximum_inventory_residual),
        'filled_orders_before_reference_endpoint':total_orders,'DCA_entry_fills_before_reference_endpoint':dca_orders,
        'one_sided_filled_notional_over_initial_capital':float(total_notional/1000),
        'entry_exit_commission_model_usdt':float(total_notional*fee_rate),
        'entry_exit_execution_proxy_model_usdt':float(total_notional*proxy_rate),
        'hypothetical_terminal_exit_cost_reserve_usdt':float(total_reserve),
        'native_terminal_funding_total_usdt':float(sum((p['funding'] for p in pairs.values()),D(0))),
        'funding_scope':'native terminal per-campaign aggregate, not observed settlement timeline; does not resolve funding/fill order ambiguity',
        'direction_exposure':{'long_notional_snapshot_mean':float(long_gross.mean()),'short_notional_snapshot_mean':float(short_gross.mean()),
                              'gross_notional_snapshot_mean':float(gross.mean()),'net_notional_snapshot_mean':float(net.mean()),
                              'net_notional_min':float(net.min()),'net_notional_max':float(net.max()),
                              'long_active_snapshot_fraction':float((long_gross>0).mean()),'short_active_snapshot_fraction':float((short_gross>0).mean())},
        'terminal_filled_open_positions':open_positions,'terminal_filled_open_position_count':len(open_positions),
        'holding_age_hours':{'natural_completed_mean':mean(completed_ages) if completed_ages else None,
                             'natural_completed_median':median(completed_ages) if completed_ages else None,
                             'natural_completed_max':max(completed_ages) if completed_ages else None,
                             'terminal_open_mean':mean(open_ages) if open_ages else None,
                             'terminal_open_max':max(open_ages) if open_ages else None,
                             'definition':'first filled entry to natural close or reference endpoint; partial exits remain one campaign'},
        'natural_completed_campaigns_before_reference_endpoint':sum(c['naturally_closed_before_endpoint'] for c in campaigns),
        'campaigns':campaigns,'pairs':rows,
        'model_terminal_attribution_reconciled':bool(contribution_available),
        'max_positive_pair_contribution_share':max((r['positive_contribution_share'] for r in rows),default=None) if contribution_available and positive_pair else None,
        'max_positive_campaign_contribution_share':max((max(0,c['reference_campaign_contribution_usdt'])/positive_campaign for c in campaigns),default=None) if contribution_available and positive_campaign else None,
        'contribution_limit':'Descriptive whole-campaign terminal model attribution, not causal event impact or independent alpha evidence; unresolved attribution returns null.',
        'real_orders':False,'financial_backtests':0,
    }
