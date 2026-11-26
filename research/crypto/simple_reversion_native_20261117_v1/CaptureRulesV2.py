"""Read-only flags/quotes receipt; no economics or source calculation."""
import hashlib

import numpy as np
import pandas as pd


def signal_receipt(frame, strategy):
    fields=['open','high','low','close','volume','enter_long','enter_short','exit_long','exit_short']
    subset=frame.loc[frame.date.ge(pd.Timestamp('2025-01-01T00:00Z')) &
                     frame.date.lt(pd.Timestamp('2026-09-01T00:00Z'))]
    dates=pd.DatetimeIndex(subset.date).as_unit('ns').asi8
    assert all(name in subset for name in fields[:5]),'OHLCV columns required'
    values=subset.reindex(columns=fields).fillna(0).to_numpy(dtype='<f8',copy=True)
    return dict(strategy=strategy,rows=len(subset),fields=fields,
        sha256=hashlib.sha256(dates.tobytes()+np.ascontiguousarray(values).tobytes()).hexdigest())
