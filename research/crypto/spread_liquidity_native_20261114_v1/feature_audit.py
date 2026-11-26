"""Independent scalar input/feature audit; never used by a trading strategy."""
import json
import math
import statistics
from pathlib import Path

import pandas as pd

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save

HERE = Path(__file__).resolve().parent


def scalar_edge(rows):
    log = [[math.log(float(v)) for v in row] for row in rows]
    returns, flags = [], []
    for old, now in zip(log[:-1], log[1:], strict=True):
        o, h, l, _c = now
        _o1, h1, l1, c1 = old
        m, m1 = (h+l)/2, (h1+l1)/2
        tau = float(h != l or l != c1)
        returns.append((m-o, o-m1, m-c1, c1-m1, o-c1))
        flags.append((tau, tau*(o != h), tau*(o != l), tau*(c1 != h1), tau*(c1 != l1)))
    mean = lambda items: math.fsum(items)/len(items)
    probabilities = [mean([row[j] for row in flags]) for j in range(5)]
    pt, po, pc = probabilities[0], sum(probabilities[1:3]), sum(probabilities[3:5])
    if sum(row[0] for row in flags)<2 or po==0 or pc==0:
        return float('nan')
    averages = [mean([row[j] for row in returns]) for j in range(5)]
    first, second = [], []
    for r, flag in zip(returns, flags, strict=True):
        r1,r2,r3,r4,r5=r
        tau=flag[0]
        d1,d3,d5=r1-averages[0]/pt*tau,r3-averages[2]/pt*tau,r5-averages[4]/pt*tau
        first.append(-4/po*d1*r2-4/pc*d3*r4)
        second.append(-4/po*d1*r5-4/pc*d5*r4)
    e1,e2=mean(first),mean(second)
    v1=mean([x*x for x in first])-e1*e1
    v2=mean([x*x for x in second])-e2*e2
    squared=(v2*e1+v1*e2)/(v1+v2) if v1+v2>0 else (e1+e2)/2
    return math.sqrt(abs(squared))


def main():
    plan=json.loads((HERE/'plan.json').read_text())
    out=Path(plan['output_root'])
    quotes=pd.read_parquet(out/'quote_volumes.parquet')
    features=pd.read_parquet(out/'preflight_features_EdgeLiquidity.parquet')
    rows=[]
    for pair in plan['pairs']:
        symbol=pair.split('/')[0]
        daily=pd.read_feather(Path(plan['datadir'])/f'futures/{symbol}_USDT_USDT-1d-futures.feather').set_index('date')
        quote=quotes.loc[quotes.pair.eq(pair)].set_index('date').quote_volume
        native=features.loc[features.pair.eq(pair)].set_index('decision_date')
        errors={'edge30':[],'amihud30':[],'lowvol63':[]}
        for decision in pd.date_range(plan['start'],plan['comparison_end'],freq='D'):
            prior=decision-pd.Timedelta(days=1)
            short=daily.loc[prior-pd.Timedelta(days=29):prior]
            full=daily.loc[prior-pd.Timedelta(days=63):prior]
            if len(short)!=30 or len(full)!=64:raise ValueError('Raw fixed-window gap')
            estimate=scalar_edge(short[['open','high','low','close']].to_numpy())
            close=full.close.tolist()
            returns=[b/a-1 for a,b in zip(close[:-1],close[1:],strict=True)]
            dollars=quote.reindex(full.index[-30:]).tolist()
            scalar={'edge30':estimate,'amihud30':math.fsum(abs(r)/q for r,q in zip(returns[-30:],dollars,strict=True))/30,
                    'lowvol63':-statistics.stdev(returns)}
            for field,value in scalar.items():
                error=abs(value-float(native.at[decision,field]))
                if not math.isfinite(error) or error>1e-11:
                    raise ValueError(f'Independent feature mismatch {pair} {decision} {field}: {error}')
                errors[field].append(error)
        # Multiplying every OHLC by a constant must leave a percentage spread unchanged.
        scale=abs(scalar_edge(short[['open','high','low','close']].to_numpy()*100)-estimate)
        if scale>1e-11:raise ValueError('EDGE price-unit sensitivity')
        rows.append({'pair':pair,'days':len(errors['edge30']),'maximum_errors':{k:max(v) for k,v in errors.items()},'scale_100_error':scale})
    save(HERE/'feature_audit.json',{'passed':True,'independent_math_fsum_statistics':True,
        'actual_native_preflight_features_checked':sum(r['days']*3 for r in rows),
        'primary_estimator_unchanged':True,'used_for_strategy_inputs':False,'records':rows})
    print('Independent scalar audit passed',sum(r['days']*3 for r in rows),'feature cells')


if __name__=='__main__':main()
