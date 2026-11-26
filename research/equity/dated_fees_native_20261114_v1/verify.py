"""Verify component artifacts and preserve the zero historical-account boundary."""
import hashlib
import inspect
import json
import platform
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from bootstrap import boot
manifest=boot()
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.persistence import LocalTrade, Order
from freqtrade.wallets import Wallets
from freqtrade.optimize.equity_backtesting import EquityBacktesting
from freqtrade.equity_fee_wallets import EquityFeeWallets
from freqtrade.data.history.equity import EquityAdmissionError
from unittest.mock import patch

def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main():
    p=read(HERE/'protocol.json')
    amendment=read(HERE/'sizing_amendment.json')
    assert sha(HERE/'protocol.json')==amendment['original_protocol_sha256']
    assert sha(amendment['preserved_old_source'])==amendment['old_sha256']
    owners=dict(p['implementation'])
    assert owners[amendment['owner']]==amendment['old_sha256']
    owners[amendment['owner']]=amendment['new_sha256']
    for path,h in owners.items():assert sha(path)==h,path
    previous=read(HERE.parent/'historical_execution_native_20261114_v1/protocol.json')
    for path,h in previous['implementation'].items():assert sha(path)==h,path
    assert sha(p['parent_plan'])==p['parent_plan_sha256']
    logs={'tests_final.log':41,'execution_regression.log':46,'settlement_regression.log':33,'history_provider_regression.log':30}
    for name,count in logs.items():
        text=(HERE/name).read_text();assert re.search(rf'\b{count} passed\b',text),name
        assert not re.search(r'\bFAILED\b|\bERROR\b',text),name
    receipts=read(HERE/'source_receipts.json')+[read(HERE/'paper_receipt.json')]
    for r in receipts:
        if 'path' in r:assert sha(r['path'])==r['sha256'],r['path']
    for name in ['native_document_receipts.json','native_pdf_document_receipts.json']:
        for row in read(HERE/name):
            assert row['result']['status']=='ok' and row['result']['truncated'] is False
    readiness=read(HERE/'investment_input_readiness.json')
    assert len(readiness['records'])==514 and readiness['new_factor_scores']==0
    for path,h in readiness['bindings'].items():assert sha(path)==h,path
    trace=read(HERE/'native_synthetic_trace.json')
    assert trace['native_fills']==8 and trace['snapshots']==10 and trace['actual_historical_strategy_accounts']==0
    assert all(s['independent_audit_passed']for s in trace['scenarios'])
    assert read(HERE/'caption_receipt.json')['bytes']==0
    with patch.object(Backtesting,'__init__')as native:
        try:EquityBacktesting({})
        except EquityAdmissionError:pass
        else:raise AssertionError('Incomplete account must reject')
        native.assert_not_called()
    paths={c.__name__:str(Path(inspect.getfile(c)).resolve())for c in [Backtesting,LocalTrade,Order,Wallets,EquityFeeWallets]}
    for name in ['Backtesting','LocalTrade','Order','Wallets']:assert '/CZ-Agent/vendor/freqtrade/'in paths[name],paths[name]
    result={'verified_utc':datetime.now(UTC).isoformat(),'passed':True,'native_commit':manifest['commit'],'native_original_files_verified':len(manifest['files']),'python':platform.python_version(),'native_runtime_paths':paths,'unique_component_checks':sum(logs.values()),'check_logs':{n:{'passed':c,'sha256':sha(HERE/n)}for n,c in logs.items()},'legacy_modules_byte_identical':True,'new_modules_sha256':owners,'native_synthetic_fills':8,'synthetic_snapshots':10,'financial_accounts':0,'net_performance':None,'factor_analysis_calls':0,'accepted_new_alphas':0,'objective_achieved':False}
    (HERE/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
