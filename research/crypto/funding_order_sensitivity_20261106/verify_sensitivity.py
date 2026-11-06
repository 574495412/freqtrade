"""Verify retained diagnostic curves and the frozen promotion gates."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from audit import HERE, ROOT, PLAN, RESULT, LONG, SHORT, CASH, now, sha, verify, write, metrics


def audit():
    plan = json.loads(PLAN.read_text())
    result = json.loads(RESULT.read_text())
    verify(plan)
    if result['registration_sha256']!=sha(PLAN.read_bytes()):
        raise ValueError('Sensitivity registration mismatch')
    by_id = {item['run']['id']:item['run'] for item in plan['cases']}
    rows = result['rows']
    if len(rows)!=12 or set(by_id)!={r['id'] for r in rows}:
        raise ValueError('Frozen account set changed')
    for row in rows:
        path = ROOT/row['output']
        if sha(path.read_bytes())!=row['output_sha256']:
            raise ValueError('Retained sensitivity curve hash differs')
        frame = pd.read_parquet(path)
        case = by_id[row['id']]
        if not frame.index.equals(pd.date_range(case['start'],case['end'],freq='5min',inclusive='left')):
            raise ValueError('Sensitivity observation calendar changed')
        original = frame.frozen_native.to_numpy()
        lo,hi = frame.one_book_lower.to_numpy(),frame.one_book_upper.to_numpy()
        if np.any(lo>hi+1e-9) or not all(abs(frame[column].iloc[0]-CASH)<1e-9 for column in frame):
            raise ValueError('Funding envelopes crossed or flat inception changed')
        for key, summary in row['variants'].items():
            values = frame[key].to_numpy()
            if metrics(values)!={k:summary[k] for k in metrics(values)}:
                raise ValueError('Retained sensitivity metrics disagree')
            if (summary['terminal_change_vs_frozen_usdt']!=float(values[-1]-original[-1])
                    or summary['max_absolute_path_change_usdt']!=float(np.max(np.abs(values-original)))):
                raise ValueError('Sensitivity deltas disagree with the full retained path')
        mdd_lo = max(0.,float(np.max(1-hi/np.maximum.accumulate(lo))))
        mdd_hi = float(np.max(1-lo/np.maximum.accumulate(hi)))
        if [mdd_lo,mdd_hi]!=row['mdd_bounds']:
            raise ValueError('Drawdown bounds disagree')
        for col in ('public_ms_once_before_exact_ties','public_ms_once_after_exact_ties'):
            values = frame[col].to_numpy()
            if np.any(values<lo-1e-9) or np.any(values>hi+1e-9):
                raise ValueError('Fixed exact-order scenario exceeds bound')

    cohorts = {}
    for row in rows:
        cohorts.setdefault((row['window'],row['cost']),{})[row['strategy']] = row
    pairs = []
    for (window,cost),strategies in cohorts.items():
        f2, control = (strategies[key] for key in ('F2FuturesCandleCost','AverageFuturesCandleControl'))
        f2l,f2u = (f2['variants'][f'one_book_{edge}']['net_return'] for edge in ('lower','upper'))
        cl,cu = (control['variants'][f'one_book_{edge}']['net_return'] for edge in ('lower','upper'))
        fl,fu = f2['mdd_bounds']
        ml,mu = control['mdd_bounds']
        pairs.append(dict(window=window,cost=cost,
                          f2_net_return_bounds=[f2l,f2u],control_net_return_bounds=[cl,cu],
                          f2_minus_control_return_bounds=[f2l-cu,f2u-cl],
                          drawdown_improvement_bounds=[1-fu/ml,1-fl/mu],
                          control_can_be_positive=cu>0,
                          frozen_f2_profit_retention_gate_passed=False if cu<=0 else None))
    for path in (LONG,SHORT):
        verify(json.loads(path.read_text()))
    candidates = [r for r in rows if r['strategy'].startswith(('F1','F2'))]
    best = max(candidates,key=lambda r:r['variants']['one_book_upper']['net_return'])
    variants = [m for row in rows for m in row['variants'].values()]
    return dict(observed_at_utc=now(), input_hashes={str(path.relative_to(ROOT)):sha(path.read_bytes())
                                               for path in (Path(__file__),PLAN,RESULT,LONG,SHORT)},
                preserved_accounts=len(rows),five_minute_points=sum(row['five_minute_samples'] for row in rows),
                maximum_native_baseline_nav_error_usdt=max(row['baseline_max_nav_error_usdt'] for row in rows),
                maximum_terminal_absolute_difference_usdt=max(abs(m['terminal_change_vs_frozen_usdt']) for m in variants),
                maximum_path_absolute_difference_usdt=max(m['max_absolute_path_change_usdt'] for m in variants),
                maximum_shift_only_terminal_difference_usdt=max(abs(row['variants']['public_ms_native_inclusive']
                    ['terminal_change_vs_frozen_usdt']) for row in rows),
                max_f1_terminal_absolute_difference_usdt=max(abs(m['terminal_change_vs_frozen_usdt'])
                    for row in rows if row['strategy']=='F1FuturesCandleCost' for m in row['variants'].values()),
                profitable_variant_accounts=sum(any(x['net_return']>0 for x in row['variants'].values()) for row in rows),
                best_bounded_factor_account=best['id'],best_bounded_factor_net_return=best['variants']['one_book_upper']['net_return'],
                f2_matched_pairs=pairs, historical_account_funding_verified=False,
                full_RD1_economic_comparison_complete=False,financial_accounts=0)


if __name__=='__main__':
    write(HERE/'verification.json',audit())
