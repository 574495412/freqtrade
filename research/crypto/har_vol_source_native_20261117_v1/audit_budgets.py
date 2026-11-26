"""Read native intents and fills, not candidate calculation code."""
import gzip
import importlib.util
import json
import math
from decimal import Decimal as D, ROUND_DOWN
from pathlib import Path

import pandas as pd

HERE=Path(__file__).absolute().parent


def main():
    plan=json.loads((HERE/'plan.json').read_text());spec=importlib.util.spec_from_file_location('native_archive_reader',HERE/'audit_accounts.py');audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit);rows=[]
    for c in plan['candidates']:
        directory=Path(plan['output_root'])/(c['name']+f"_{c['cost_bps']}bps")
        with gzip.open(directory/'strategy_trace.json.gz','rt')as f:trace=json.load(f)
        weights=pd.DataFrame(trace['weights']).set_index('day');weights.index=pd.to_datetime(weights.index,utc=True)
        mode=c['name'].split('_')[0];reference=pd.read_parquet(Path(plan['output_root'])/('preflight_'+mode+'_1000_conservative_admission1')/'native_weights.parquet')
        pd.testing.assert_frame_equal(weights,reference,check_freq=False,check_names=False)
        nav=pd.read_parquet(directory/'hourly_equity.parquet').set_index('date').hourly_mark_equity
        grid=pd.date_range('2025-01-01T01:00Z','2026-08-31T01:00Z',freq='D').as_unit('ns')
        clocks=pd.DatetimeIndex(pd.to_datetime([r['clock']for r in trace['budgets']],utc=True)).as_unit('ns');assert clocks.equals(grid)
        markets=json.loads(Path(c['market_snapshot']['path']).read_text())['markets'];maximum=0.;checked=0
        for r in trace['budgets']:
            stamp=pd.Timestamp(r['clock']);maximum=max(maximum,abs(r['native_marked_equity']-nav.at[stamp]));assert maximum<1e-6
            for pair,target in r['target_quantities'].items():
                step=D(str(markets[pair]['precision']['amount']));price=r['prices'][pair]
                raw=D('.99')*D(str(r['native_marked_equity']))*D(str(weights.at[stamp.floor('D'),pair]))/D(str(price))
                expected=float((raw/step).to_integral_value(rounding=ROUND_DOWN)*step)
                limits=markets[pair]['limits'];minimum=max((limits['cost']['min']or 0)*1.5,(limits['amount']['min']or 0)*price*1.05)
                if expected*price<minimum:expected=0.
                assert abs(expected-target)<1e-10,(c['name'],stamp,pair,expected,target);checked+=1
        fits=[]
        for pair,records in trace['fits'].items():
            for f in records:
                assert pd.Timestamp(f['last_train_feature_day'])<pd.Timestamp(f['origin'])
                assert pd.Timestamp(f['latest_realised_target_day'])<=pd.Timestamp(f['origin'])
            fits.append(dict(pair=pair,folds=len(records),minimum_valid_train_rows=min(f['train_rows']for f in records)))
        stats,_=audit.native_stats(directory,c['strategy']);nonterminal=[]
        for t in stats['trades']:
            for i,o in enumerate(t['orders']):
                if i==len(t['orders'])-1 and t['exit_reason']=='force_exit':continue
                stamp=pd.to_datetime(o['order_filled_timestamp'],unit='ms',utc=True)
                assert stamp.hour==1 and stamp.minute==stamp.second==0;nonterminal.append(stamp)
        rows.append(dict(account=c['name'],passed=True,daily_budgets=len(clocks),target_quantities=checked,maximum_native_budget_nav_error_usdt=maximum,folds=fits,nonterminal_fills_at_registered_clock=len(nonterminal),source_weights_equal_across_fees_and_capital=True))
    (HERE/'budget_audit.json').write_text(json.dumps(dict(passed=True,accounts=rows,complete_daily_budgets=sum(r['daily_budgets']for r in rows),independently_checked_quantities=sum(r['target_quantities']for r in rows),candidate_imported=False),indent=2)+'\n')
    print('AUDITED',len(rows),'nativeallocationledgers')


if __name__=='__main__':main()
