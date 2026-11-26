"""Source-preserving funding characteristic and native strategy signal inputs.

No return/account calculation. Missing funding days remain unknown. Quantile
ties and source short-overwrite semantics are retained and explicitly audited.
"""
import numpy as np
import pandas as pd

ANCHOR=pd.Timestamp('2023-01-09',tz='UTC')


def observed_daily_funding(events,pairs,start,end,invalid_days=()):
    frame=events.copy()
    if frame[['pair','date']].duplicated().any():raise ValueError('Duplicate funding event')
    if not set(frame.pair.unique()).issubset(pairs):raise ValueError('Unknown funding pair')
    if not np.isfinite(frame.last_funding_rate).all():raise ValueError('Nonfinite observed rate')
    frame['day']=frame.event_time.dt.floor('D')
    available=frame.event_time+pd.Timedelta(minutes=15)
    frame['known']=available.le(frame.day+pd.Timedelta(days=1))
    # This aggregation never fills an absent event/day with a zero payment.
    daily=frame.groupby(['day','pair']).last_funding_rate.sum(min_count=1).unstack('pair')
    days=pd.date_range(start,end,freq='D')
    daily=daily.reindex(index=days,columns=pairs)
    for pair,day in invalid_days:
        label=pd.Timestamp(day).floor('D')
        if label in daily.index:daily.loc[label,pair]=np.nan
    for (day,pair),group in frame.groupby(['day','pair']):
        if not group.known.all() and day in daily.index:daily.loc[day,pair]=np.nan
    return daily


def source_inputs(daily):
    if not daily.index.is_unique or not daily.index.is_monotonic_increasing:
        raise ValueError('Require an explicit unique chronological daily calendar')
    if not pd.DatetimeIndex(daily.index).equals(pd.date_range(daily.index[0],daily.index[-1],freq='D')):
        raise ValueError('Never compress missing calendar days')
    observed=daily.rolling(7,min_periods=7).mean().shift(1)
    weights=pd.DataFrame(np.nan,index=daily.index,columns=daily.columns)
    diagnostics=[]
    for day in daily.index:
        if day<ANCHOR or (day-ANCHOR).days%5:continue
        score=observed.loc[day].dropna()
        if len(score)<6:continue
        lo,hi=score.quantile(.33),score.quantile(.67)
        longs=score[score<=lo].index;shorts=score[score>=hi].index
        row=pd.Series(0.,index=daily.columns)
        row.loc[longs]=.5/len(longs)
        row.loc[shorts]=-.5/len(shorts)  # exact source assignment ordering, including ties
        weights.loc[day]=row
        diagnostics.append({'date':day.isoformat(),'valid_assets':len(score),'long_members':list(longs),
            'short_members':list(shorts),'overlap_members':list(longs.intersection(shorts)),
            'net_weight':float(row.sum()),'gross_weight':float(row.abs().sum())})
    weights=weights.ffill().fillna(0.)  # cash before first eligible source rebalance, not factor imputation
    return {'funding7':observed,'factor':-observed,'weights':weights,'decisions':diagnostics}
