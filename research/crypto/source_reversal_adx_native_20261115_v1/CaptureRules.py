"""Canonical read-only receipts for actual native signal/indicator values."""
import hashlib

import numpy as np
import pandas as pd


def signal_receipt(frame, strategy):
    fields = ['open','high','low','close','volume','enter_long','exit_long']
    if strategy in {'SourceReversal','ReversalInADXTrend'}:
        fields += ['slowk','rsi','fisher_rsi','bb_lowerband','sar','CDLHAMMER']
    if strategy == 'SourceADX':
        fields += ['adx','plus_di','minus_di','sar','mom']
    if strategy == 'ReversalInADXTrend':
        fields += ['adx_1h','plus_di_1h','minus_di_1h','mom_1h','adx_source_long_1h']
    selected = frame.loc[frame.date.between(pd.Timestamp('2025-01-01T00:00Z'),pd.Timestamp('2026-08-31T23:59:59Z'))]
    digest = hashlib.sha256()
    dates = pd.DatetimeIndex(selected.date).as_unit('ns').asi8
    digest.update(dates.tobytes())
    for field in fields:
        values = selected[field].to_numpy(dtype=np.float64,copy=True)
        if field in {'enter_long','exit_long'}:values[np.isnan(values)] = 0
        else:values[np.isnan(values)] = np.nan
        digest.update(field.encode());digest.update(values.tobytes())
    return dict(sha256=digest.hexdigest(),rows=len(selected),fields=fields,
        entries=int(selected.enter_long.fillna(0).eq(1).sum()),
        exits=int(selected.exit_long.fillna(0).eq(1).sum()))
