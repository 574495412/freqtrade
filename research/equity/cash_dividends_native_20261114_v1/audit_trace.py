"""Independent Decimal audit of exported actual events; no trade generation."""
import json
from datetime import datetime
from decimal import Decimal as D, ROUND_HALF_UP
from pathlib import Path
h=Path(__file__).resolve().parent
x=json.loads((h/'native_synthetic_trace.json').read_text());checks=0
for scenario in x['scenarios']:
 # Synthetic test contract: gross DPS10,10% withholding; ex-open Jan3;
 # broker credit Jan6 12UTC; independent per-filled-order fee formula.
 ex=datetime.fromisoformat('2025-01-03T'+('14'if scenario['market']=='XNYS'else'01')+':00:00+00:00')
 pay=datetime.fromisoformat('2025-01-06T12:00:00+00:00')
 tape=sorted(scenario['orders'],key=lambda o:(o['at'],o['order_id']))
 entitlement=sum((D(o['quantity'])*(1 if o['side']=='buy'else -1)for o in tape
  if datetime.fromisoformat(o['at'])<ex),D(0))
 gross=(entitlement*D(10)).quantize(D('.01'),rounding=ROUND_HALF_UP)
 tax=(gross*D('.1')).quantize(D('.01'),rounding=ROUND_HALF_UP);net=gross-tax
 for s in scenario['snapshots']:
  at=datetime.fromisoformat(s['at']);qty=D(0);cash=D(1000);fees=D(0)
  # Same-clock pre-fill and post-fill observations differ by actual native
  # completed-order count. A timestamp alone cannot determine that boundary.
  for o in tape[:s['filled_orders']]:
   assert datetime.fromisoformat(o['at'])<=at
   q,p=D(o['quantity']),D(o['price']);v=q*p;buy=o['side']=='buy'
   fee=D(1)+(v*D('.002')if not buy else D(0));fee=fee.quantize(D('.01'),rounding=ROUND_HALF_UP)
   fees+=fee;cash+=(-v if buy else v)-fee;qty+=q if buy else -q
  receivable=net if ex<=at<pay else D(0);paid=net if at>=pay else D(0);cash+=paid
  market_value=sum((D(str(q))*D(str(m['price']))for _,q,m in s['holdings']),D(0))
  actual_qty=sum((D(str(q))for _,q,_ in s['holdings']),D(0))
  for actual,expected in[(s['total_cash'],cash),(s['net_nav'],cash+market_value+receivable),
   (s['execution_fees'],fees),(s['dividend_receivable'],receivable),(s['paid_dividends'],paid),
   (actual_qty,qty),(s['free_cash']+s['unsettled_receivable'],cash)]:
   assert abs(D(str(actual))-expected)<D('.00000001'),(scenario['market'],s['at'],actual,expected)
   checks+=1
out={'passed':True,'independent_decimal_cells':checks,'native_fills':6,'snapshots':12,
 'checks':'cash, actual quantity, actual order fees, net receivable, paid cash, NAV, cash partition',
 'historical_accounts':0,'financial_engine_replaced':False}
(h/'trace_audit.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out))
