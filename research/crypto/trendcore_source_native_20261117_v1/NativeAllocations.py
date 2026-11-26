"""Read actual native filled orders for a marked allocation budget; no simulator."""
import math


def open_ledger_value(trade,price):
    entries=[];exits=[]
    for order in trade.orders:
        if order.ft_is_open or not order.filled:continue
        (entries if order.ft_order_side==trade.entry_side else exits).append(order)
    entry=math.fsum(o.filled*o.safe_price for o in entries)
    exit_=math.fsum(o.filled*o.safe_price for o in exits)
    quantity=math.fsum(o.filled for o in entries)-math.fsum(o.filled for o in exits)
    if abs((quantity-trade.amount)*price)>1e-6:raise ValueError('Native quantity ledger does not reconcile')
    sign=-1 if trade.is_short else 1
    return sign*(exit_+trade.amount*price-entry)-entry*trade.fee_open-exit_*trade.fee_close-trade.amount*price*trade.fee_close+(trade.funding_fees or 0.)


def marked_budget(capital,closed_profit,trades,marks):
    equity=capital+closed_profit+math.fsum(open_ledger_value(t,marks[t.pair]) for t in trades)
    if not math.isfinite(equity) or equity<=0:raise ValueError('Nonpositive marked native account')
    return equity
