"""Verify real component evidence; never promote it to a strategy backtest."""
import hashlib
import inspect
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from bootstrap import boot

manifest=boot()
from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.equity_backtesting import EquityBacktesting
from freqtrade.optimize.equity_nav import capture_equity_nav
from freqtrade.persistence import LocalTrade, Order
from freqtrade.wallets import Wallets

HERE=Path(__file__).resolve().parent
def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

protocol=read(HERE/'protocol.json')
for path,h in protocol['unchanged_parent_modules'].items():assert sha(path)==h,path
implementation=read(HERE/'implementation.json')
for path,h in implementation.items():assert sha(path)==h,path
log=(HERE/'regression_final.log').read_text()
assert re.search(r'\b187 passed\b',log) and not re.search(r'\bFAILED\b|\bERROR\b',log)
assert (HERE/'lint_final.log').read_text().strip()=='All checks passed!'
assert (HERE/'semantic_lint.log').read_text().strip()=='All checks passed!'
receipts=read(HERE/'source_receipts.json')+[read(HERE/'source_followup_receipt.json'),read(HERE/'caption_receipt.json')]
for row in receipts:
    if 'path' in row:assert sha(row['path'])==row['sha256'],row['path']
assert all(r['status']==200 for r in receipts[:4])
admission=read(HERE/'customer_input_admission.json')
assert len(admission['records'])==514 and admission['bound_annual_input_count']==495
for row in admission['records']:assert sha(row['source'])==row['sha256'],row['source']
for path,h in admission['bindings'].items():assert sha(path)==h,path
gap=read(HERE/'source_staleness_diagnostic.json')
assert gap['source_sha256']==sha(HERE/'sources/CustomerMomentum.py')
assert gap['observed_stale_months_after_first_12']==23
trace=read(HERE/'native_synthetic_trace.json')
assert trace['native_fills']==8 and trace['native_nav_snapshots']==12
assert trace['historical_strategy_accounts']==0 and trace['historical_performance'] is None
for row in trace['scenarios']:
    assert row['passed'] and len(row['all_snapshots'])==6
    for s in row['all_snapshots']:
        assert abs(s['net_nav']-s['total_cash']-s['market_value'])<1e-8
        assert abs(s['total_cash']-s['free_cash']-s['unsettled_receivable'])<1e-8
    assert abs(row['component_result']['final_net_nav']-974.98)<1e-8
with patch.object(Backtesting,'__init__')as init:
    try:EquityBacktesting({})
    except EquityAdmissionError:pass
    else:raise AssertionError('Incomplete full equity account must still reject')
    init.assert_not_called()
paths={c.__name__:inspect.getfile(c)for c in [Backtesting,LocalTrade,Order,Wallets]}
assert all('/CZ-Agent/vendor/freqtrade/'in p for p in paths.values())
result={'verified_utc':datetime.now(UTC).isoformat(),'passed':True,
        'native_commit':manifest['commit'],'native_original_files_verified':len(manifest['files']),
        'runtime_paths':paths,'local_nav_owner':inspect.getfile(capture_equity_nav),
        'parent_modules_byte_identical':True,'unique_checks':187,'new_checks':37,
        'native_synthetic_fills':8,'native_nav_snapshots':12,
        'input_rows_verified':514,'source_staleness_gap_months':23,
        'historical_strategy_accounts':0,'bench_records':0,'new_registry_definitions':0,
        'factor_analysis_calls':0,'accepted_new_alphas':0,'historical_performance':None,
        'objective_achieved':False,'orders':False,'deployment':False,'services_changed':False}
(HERE/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
