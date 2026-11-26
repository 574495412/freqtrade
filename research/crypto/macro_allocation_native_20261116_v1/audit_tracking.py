"""Post-result tracking diagnostics from actual orders; no candidate policy import."""
import gzip
import importlib.util
import json
import math
from pathlib import Path

import pandas as pd

HERE=Path(__file__).absolute().parent


def main():
    plan=json.loads((HERE/'plan.json').read_text())
    spec=importlib.util.spec_from_file_location('actual_export_reader',HERE/'audit_accounts.py')
    audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
    rows=[];parities=[]
    for candidate in plan['candidates']:
        directory=Path(plan['output_root'])/(candidate['name']+f"_{candidate['cost_bps']}bps")
        trace=json.load(gzip.open(directory/'strategy_trace.json.gz','rt'))
        stats,_=audit.native_stats(directory,candidate['strategy'])
        orders=[o for t in stats['trades'] for o in t['orders']]
        events=[(o['order_filled_timestamp'],o['amount']*(1 if o['ft_is_entry'] else -1)) for o in orders]
        errors=[];omitted=exact=0
        for budget in trace['budgets']:
            ms=int(pd.Timestamp(budget['clock']).timestamp()*1000)
            held=math.fsum(q for ts,q in events if ts<=ms)
            target=budget['target_quantities']['BTC/USDT']
            errors.append(abs(held-target)*budget['prices']['BTC/USDT'])
            exact+=abs(held-target)<1e-10
            omitted+=not any(ts==ms for ts,q in events) and abs(held-target)>1e-10
        rows.append(dict(account=candidate['name'],budget_clocks=len(errors),
            actual_post_clock_quantity_matches_target=exact,remaining_target_differences_without_fill=omitted,
            max_actual_target_notional_gap_usdt=max(errors,default=0),
            meaning='actual filled quantity may differ from target under native limits;not a free weight reset'))
    for capital in [1000,500]:
        for scenario,cost in [('conservative',20),('lower',13)]:
            paths=[Path(plan['output_root'])/(f'{role}_{capital}_{scenario}_{cost}bps') for role in ['full','hold']]
            frames=[pd.read_parquet(d/'hourly_equity.parquet') for d in paths]
            pd.testing.assert_frame_equal(frames[0],frames[1])
            trades=[audit.native_stats(d,st)[0]['trades'] for d,st in zip(paths,['DailyFullBTC','BuyHoldBTC'],strict=True)]
            assert trades[0]==trades[1]
            parities.append(dict(capital=capital,scenario=scenario,actual_trades_orders_and_full_NAV_exact=True))
    result=dict(passed=True,accounts=rows,full_hold_actual_financial_parity=parities,
        all_quantities_from_actual_exported_orders=True)
    (HERE/'target_tracking.json').write_text(json.dumps(result,indent=2)+'\n')
    print('Tracking audited;4full/hold actual financial parities')


if __name__=='__main__':main()
