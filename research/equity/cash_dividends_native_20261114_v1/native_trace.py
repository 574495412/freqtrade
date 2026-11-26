"""Export synthetic native fills and net claims; no historical returns."""
import json
from pathlib import Path
from unittest.mock import patch
from test_dividends import trace
from freqtrade.persistence import LocalTrade,Trade
h=Path(__file__).resolve().parent
old=Trade.use_db;Trade.use_db=False
try:
 with patch.object(Trade,'commit'):
  rows=[]
  for market in('XNYS','XHKG'):
   LocalTrade.reset_trades();rows.append(trace(market))
finally:LocalTrade.reset_trades();Trade.use_db=old
out={'evidence':'synthetic actual native fill boundaries; not Backtesting.start historical account',
 'native_fills':sum(r['native_fills']for r in rows),'snapshots':sum(len(r['snapshots'])for r in rows),
 'scenarios':rows,'historical_strategy_accounts':0,'performance':None,'bench_records':0}
(h/'native_synthetic_trace.json').write_text(json.dumps(out,indent=2,default=str)+'\n')
print(json.dumps({k:v for k,v in out.items()if k!='scenarios'}))
