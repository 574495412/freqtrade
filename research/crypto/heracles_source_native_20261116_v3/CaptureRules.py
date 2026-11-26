"""Read-only flags/quotes receipt; no economics or source calculation."""
import hashlib

import numpy as np
import pandas as pd


def signal_receipt(frame, strategy):
    fields=['open','high','low','close','volume','enter_long','enter_short','exit_long','exit_short']
    subset=frame.loc[frame.date.ge(pd.Timestamp('2025-01-01T00:00Z')) &
                     frame.date.lt(pd.Timestamp('2026-09-01T00:00Z'))]
    required=['open','high','low','close','volume']
    if not set(required).issubset(subset) or not np.isfinite(subset[required].to_numpy()).all():
        raise ValueError('Actual source OHLCV cannot be missing or nonfinite in signal receipt')
    missing=sorted(set(fields)-set(subset))
    if set(missing)-{'enter_long','enter_short','exit_long','exit_short'}:
        raise ValueError('Only native optional signal columns may be neutral in receipt')
    dates=pd.DatetimeIndex(subset.date).as_unit('ns').asi8
    values=subset.reindex(columns=fields).fillna(0).to_numpy(dtype='<f8',copy=True)
    return dict(strategy=strategy,rows=len(subset),fields=fields,optional_signal_columns_neutralized=missing,
        sha256=hashlib.sha256(dates.tobytes()+np.ascontiguousarray(values).tobytes()).hexdigest())


def terminal_snapshot(open_trades):
    """Read the pinned native dict-of-trade-lists without consuming or mutating it."""
    if not isinstance(open_trades,dict):
        raise TypeError('Native terminal trades must be dict[str,list[Trade]]')
    result=[]
    for pair,trades in open_trades.items():
        if not isinstance(trades,list):
            raise TypeError('Native terminal pair values must be lists')
        for trade in trades:
            if trade.pair != pair:
                raise ValueError('Native terminal pair/Trade mismatch')
            result.append({'pair':trade.pair,'trade_id':trade.id,'amount':trade.amount,
                           'open_date':str(trade.open_date_utc),
                           'has_open_position':trade.has_open_position})
    return result
