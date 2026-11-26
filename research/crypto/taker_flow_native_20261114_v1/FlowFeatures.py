"""Native strategy input adapter around the exact extracted author kernel."""
import numpy as np
import pandas as pd

from flow_author_kernel import build_flow_features

FIELDS=['cvd_20','taker_imbalance_ma_20','avg_trade_size_z_20']


def calculate_features(hours,flow):
    if hours.date.duplicated().any() or not hours.date.is_monotonic_increasing:
        raise ValueError('Require unique chronological native hourly observations')
    expected=pd.date_range(hours.date.iloc[0],hours.date.iloc[-1],freq='h')
    if not pd.DatetimeIndex(hours.date).as_unit('ns').equals(expected.as_unit('ns')):
        raise ValueError('Never compress missing native hours')
    if flow.date.duplicated().any():raise ValueError('Duplicate raw flow')
    required=['base_volume','taker_buy_base_volume','number_of_trades']
    if not set(required).issubset(flow.columns):raise ValueError('Missing observed aggressor fields')
    source=flow.set_index('date').reindex(pd.DatetimeIndex(hours.date))
    frame=source[required].rename(columns={'base_volume':'volume'}).reset_index(drop=True)
    # Zero observed activity is not a missing observation; the author kernel
    # decides each feature's denominator/support without replacing zero trades.
    known=frame.notna().all(axis=1)
    if (frame.loc[known,'taker_buy_base_volume']>frame.loc[known,'volume']+1e-8).any():
        raise ValueError('Taker buy exceeds total observed volume')
    raw=build_flow_features(frame)
    if not all(field in raw for field in FIELDS):raise ValueError('Author returned missing feature schema')
    result=pd.DataFrame({'decision_date':pd.to_datetime(hours.date.to_numpy(),utc=True)+pd.Timedelta(hours=1)})
    for field in FIELDS:
        result[field]=raw[field].to_numpy()
        if np.isinf(result[field]).any():raise ValueError('Nonfinite author result')
    return result
