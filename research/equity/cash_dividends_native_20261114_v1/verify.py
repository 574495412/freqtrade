"""Verify current local components, preserved source bytes, actual tests and limits."""
import hashlib,inspect,json,re
from datetime import UTC,datetime
from pathlib import Path
from unittest.mock import patch
from bootstrap import boot
manifest=boot()
from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.equity_backtesting import EquityBacktesting
from freqtrade.persistence import LocalTrade,Order
from freqtrade.wallets import Wallets
h=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_text())
for p,v in read(h/'implementation.json').items():assert sha(p)==v,p
amend=read(h/'technical_amendment.json')
for p,v in amend['prior_bytes_preserved'].items():assert sha(p)==v,p
for p,v in amend['current_bytes'].items():assert sha(p)==v,p
# All inherited local owners outside the explicitly amended three stay byte exact.
parent=read(h.parent/'net_nav_native_20261114_v1/protocol.json')
changed={str(h.parents[2]/p)for p in['freqtrade/equity_wallets.py','freqtrade/equity_fee_wallets.py','freqtrade/optimize/equity_nav.py']}
for p,v in parent['unchanged_parent_modules'].items():
 if p not in changed:assert sha(p)==v,p
log=(h/'regression_final.log').read_text();assert re.search(r'\b197 passed\b',log)and'FAILED'not in log
for n in['lint_final.log','semantic_lint.log']:assert(h/n).read_text().strip()=='All checks passed!'
for n in['source_receipts.json','follow_source_receipts.json','social_receipts.json']:
 for r in read(h/n):
  if 'path'in r:assert sha(r['path'])==r['sha256']
  if 'visible_text_path'in r:assert sha(r['visible_text_path'])==r['visible_text_sha256']
  if 'caption_request'in r:
   c=r['caption_request'];assert sha(c['path'])==c['sha256']and c['bytes']==0
admit=read(h/'input_admission.json');assert len(admit['records'])==514
assert admit['source_distribution_schema_count']==0 and admit['known_zero_distribution_history_count']==0
for r in admit['records']:assert sha(r['path'])==r['sha256'],r['path']
assert sha(admit['parent_receipt'])==admit['parent_sha256']
trace=read(h/'native_synthetic_trace.json');audit=read(h/'trace_audit.json')
assert trace['native_fills']==6 and trace['snapshots']==12 and trace['historical_strategy_accounts']==0
assert audit['passed']and audit['independent_decimal_cells']==84
for r in trace['scenarios']:
 assert len(r['orders'])==3
 for s in r['snapshots']:
  assert abs(s['net_nav']-s['total_cash']-s['market_value']-float(s['dividend_receivable']))<1e-8
with patch.object(Backtesting,'__init__')as init:
 try:EquityBacktesting({})
 except EquityAdmissionError:pass
 else:raise AssertionError('Incomplete historical equity account must reject')
 init.assert_not_called()
paths={c.__name__:inspect.getfile(c)for c in[Backtesting,LocalTrade,Order,Wallets]}
assert all('/CZ-Agent/vendor/freqtrade/'in p for p in paths.values())
engine=Path('/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade')
expected={p for p in manifest['files']if p.endswith('.py')}
assert {str(p.relative_to(engine))for p in engine.rglob('*.py')}==expected
out={'verified_utc':datetime.now(UTC).isoformat(),'passed':True,'native_commit':manifest['commit'],
 'original_native_files_verified':len(manifest['files']),'native_runtime_owners':paths,
 'unique_component_checks':197,'new_dividend_checks':40,'inherited_regression_checks':157,
 'native_synthetic_fills':6,'native_snapshots':12,'independent_decimal_cells':84,
 'original_inputs_verified':514,'source_factor_input_admitted':False,'original_parent_receipts_preserved':True,
 'full_equity_entry_still_rejects_before_native_initialization':True,
 'new_registry_definitions':0,'factor_analysis_calls':0,'historical_accounts':0,'combination_accounts':0,'bench_records':0,
 'accepted_new_independent_factors':0,'historical_performance':None,'objective_achieved':False,
 'orders':False,'deployment':False,'services_changed':False}
(h/'verification.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
