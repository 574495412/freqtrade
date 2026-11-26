"""Decompose marked account gain from native fills; no alpha calculator import."""
import importlib.util
import json
import math
from pathlib import Path

import pandas as pd

HERE=Path(__file__).absolute().parent


def main():
    spec=importlib.util.spec_from_file_location('independent_archive_reader',HERE/'audit_accounts.py');audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
    plan=json.loads((HERE/'plan.json').read_text());edge=pd.Timestamp(plan['comparison_end']);rows=[]
    for c in plan['candidates']:
        directory=Path(plan['output_root'])/(c['name']+f"_{c['cost_bps']}bps");stats,archive=audit.native_stats(directory,c['strategy'])
        realized=[];unrealized=[];fees=[];reserves=[];open_positions=[];excluded=[];included=0
        for t in stats['trades']:
            quantity=cost=0.
            for o in t['orders']:
                if o['order_filled_timestamp']>=edge.timestamp()*1000:
                    excluded.append(dict(pair=t['pair'],side=o['ft_order_side'],timestamp=o['order_filled_timestamp'],reason=t['exit_reason']));continue
                q=o['amount'];value=q*o['safe_price'];included+=1
                fees.append(value*(t['fee_open']if o['ft_is_entry']else t['fee_close']))
                if o['ft_is_entry']:
                    quantity+=q;cost+=value
                else:
                    assert quantity>0 and q<=quantity+1e-9
                    basis=cost/quantity
                    realized.append(value-q*basis)
                    quantity-=q;cost-=q*basis
                    if abs(quantity)<1e-10:quantity=cost=0.
            mark=float(pd.read_feather(Path(plan['datadir'])/(t['pair'].replace('/','_')+'-1h.feather')).set_index('date').loc[edge,'open'])
            unrealized.append(quantity*mark-cost);reserves.append(quantity*mark*t['fee_close'])
            if quantity>0:open_positions.append(dict(pair=t['pair'],quantity=quantity,mark=mark,cost_basis_usdt=cost,gross_unrealized_usdt=quantity*mark-cost))
        total=math.fsum(realized)+math.fsum(unrealized)-math.fsum(fees)-math.fsum(reserves)
        nav=pd.read_parquet(directory/'hourly_equity.parquet').set_index('date').hourly_mark_equity
        error=abs(total-(nav.loc[edge]-c['capital']));assert error<1e-7
        assert len(excluded)==len(open_positions)==2 and all(r['reason']=='force_exit'for r in excluded)
        rows.append(dict(account=c['name'],gross_realized_price_pnl_usdt=math.fsum(realized),gross_unrealized_price_pnl_usdt=math.fsum(unrealized),paid_quote_fees_usdt=math.fsum(fees),remaining_exit_fee_reserve_usdt=math.fsum(reserves),common_endpoint_net_pnl_usdt=total,common_endpoint_actual_fills=included,excluded_native_forced_exit_orders=excluded,open_positions=open_positions,reconciliation_error_usdt=error))
    (HERE/'endpoint_components.json').write_text(json.dumps(dict(passed=True,accounts=rows,average_cost_basis_diagnostic=True,returns_remain_native_hourly_marked_nav=True),indent=2)+'\n')
    for r in rows:print(r['account'],{k:v for k,v in r.items()if k.endswith('_usdt')})


if __name__=='__main__':main()
