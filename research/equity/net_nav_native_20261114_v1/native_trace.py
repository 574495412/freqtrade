"""Export audited synthetic native observations, not historical backtests."""
import json
from pathlib import Path
from unittest.mock import patch
from test_nav import native_trace
from freqtrade.persistence import LocalTrade, Trade

HERE=Path(__file__).resolve().parent
old=Trade.use_db
Trade.use_db=False
rows=[]
try:
    with patch.object(Trade,'commit'):
        for market in ['XNYS','XHKG']:
            LocalTrade.reset_trades()
            rows.append(native_trace(market))
finally:
    LocalTrade.reset_trades()
    Trade.use_db=old
result={'stage':'synthetic native component with independent Decimal audit',
        'native_fills':8,'native_nav_snapshots':12,'historical_strategy_accounts':0,
        'bench_records':0,'historical_performance':None,'scenarios':rows}
(HERE/'native_synthetic_trace.json').write_text(json.dumps(result,indent=2,default=str)+'\n')
print(json.dumps({k:v for k,v in result.items()if k!='scenarios'},indent=2))
