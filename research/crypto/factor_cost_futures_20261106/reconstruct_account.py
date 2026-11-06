"""Independent exported-fill NAV with the explicitly assumed native funding clock."""
import numpy as np
import pandas as pd


def funding_projection(orders,index,unit_funding):
    """Native inclusive funding intervals, restarted after each filled order.

    This models the official engine convention, including a possible repeated
    boundary on a fill at a funding timestamp. It does not certify settlements.
    """
    clock=index.as_unit('ns').asi8
    if (not unit_funding.index.is_unique or not unit_funding.index.is_monotonic_increasing
            or not np.isfinite(unit_funding.to_numpy(dtype=float)).all()):
        raise ValueError('Invalid funding inputs')
    dates=unit_funding.index.as_unit('ns').asi8
    cumulative=np.r_[0.,np.cumsum(unit_funding.to_numpy(dtype=float))]
    values=np.zeros(len(index));quantity=0.;allocated=0.;previous=None
    for order in orders:
        current=pd.Timestamp(order['order_filled_timestamp'],unit='ms',tz='UTC').value
        if previous is not None:
            if current<previous:raise ValueError('Nonterminal native fill clock went backward')
            selected=(clock>previous)&(clock<=current)
            first=dates.searchsorted(previous,'left')
            last=dates.searchsorted(clock[selected],'right')
            values[selected]=allocated-quantity*(cumulative[last]-cumulative[first])
            end=dates.searchsorted(current,'right')
            allocated-=quantity*(cumulative[end]-cumulative[first])
        quantity+=float(order['amount'])*(1 if order['ft_is_entry'] else -1)
        if abs(quantity)<1e-9:quantity=0.
        if quantity<0:raise ValueError('Negative reconstructed inventory')
        previous=current
    if previous is not None:
        selected=clock>previous
        first=dates.searchsorted(previous,'left');last=dates.searchsorted(clock[selected],'right')
        values[selected]=allocated-quantity*(cumulative[last]-cumulative[first])
    return values


def reconstruct(trades,index,marks,funding_units):
    clock=index.as_unit('ns').asi8
    pnl=np.zeros(len(index));gross=np.zeros(len(index));funding=np.zeros(len(index))
    pairs={};campaigns=[];fees=notional=0.
    for trade in trades:
        if trade['is_short'] or trade['leverage']!=1:raise ValueError('Only registered 1x long accounts')
        pair=trade['pair'];price=marks[pair].reindex(index).to_numpy(dtype=float)
        if not np.isfinite(price).all() or (price<=0).any():raise ValueError('Incomplete mark coverage')
        orders=trade['orders'][:-1] if trade['exit_reason']=='force_exit' else trade['orders']
        qty_change=np.zeros(len(index));cash_change=np.zeros(len(index));entry_count=0
        for order in orders:
            at=pd.Timestamp(order['order_filled_timestamp'],unit='ms',tz='UTC').value
            if at<clock[0]:raise ValueError('Position predates flat inception')
            where=clock.searchsorted(at,'right');amount=float(order['amount']);value=amount*float(order['safe_price'])
            entry=order['ft_is_entry'];fee=value*float(trade['fee_open'] if entry else trade['fee_close'])
            if where<len(index):
                qty_change[where]+=amount*(1 if entry else -1)
                cash_change[where]+=value*(-1 if entry else 1)-fee
                fees+=fee;notional+=value
            entry_count+=int(entry)
        if entry_count>1:raise ValueError('Unexpected DCA')
        quantity=np.cumsum(qty_change);quantity[np.abs(quantity)<1e-9]=0.
        if (quantity<0).any():raise ValueError('Negative cumulative position')
        cash=np.cumsum(cash_change)
        fund=funding_projection(orders,index,funding_units[pair])
        contribution=cash+quantity*price*(1-float(trade['fee_close']))+fund
        pnl+=contribution;gross+=quantity*price;funding+=fund
        pairs[pair]=pairs.get(pair,0.)+float(contribution[-1])
        campaigns.append(dict(pair=pair,terminal_quantity=float(quantity[-1]),terminal_marked_pnl=float(contribution[-1]),
            terminal_projected_funding=float(fund[-1]),native_terminal_funding=float(trade['funding_fees'] or 0),
            terminal_forced_exit_excluded=trade['exit_reason']=='force_exit'))
    return dict(equity=1000.+pnl,gross=gross,funding=funding,pairs=pairs,campaigns=campaigns,
        modeled_fees_before_endpoint=fees,filled_notional_before_endpoint=notional,
        funding_scope='Independent arithmetic under native inclusive model intervals; not verified settlement/fill chronology')
