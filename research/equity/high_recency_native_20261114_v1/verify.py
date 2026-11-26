"""Check actual native owners, current source, frozen sources and old receipts."""
import importlib.util
import inspect
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from bootstrap import ENGINE, HERE, ROOT, boot, sha


def main():
    owner = boot()
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.data.equity_provider import EquityDataProvider
    from freqtrade.data.history.equity import EquityAdmissionError
    from freqtrade.optimize.backtesting import Backtesting
    from freqtrade.strategy import IStrategy

    modules = {}
    for cls in [DataProvider, EquityDataProvider, IStrategy, Backtesting]:
        path = Path(inspect.getfile(cls)).resolve()
        if cls is not EquityDataProvider and not path.is_relative_to(ENGINE):
            raise ValueError('Unexpected native owner: ' + str(path))
        modules[cls.__name__] = dict(path=str(path), sha256=sha(path))
    name = 'freqtrade.optimize.equity_backtesting'
    spec = importlib.util.spec_from_file_location(name, ROOT / 'freqtrade/optimize/equity_backtesting.py')
    fence = importlib.util.module_from_spec(spec)
    sys.modules[name] = fence
    spec.loader.exec_module(fence)
    assert issubclass(fence.EquityBacktesting, Backtesting)
    with patch.object(Backtesting, '__init__') as init:
        try:
            fence.EquityBacktesting({})
        except EquityAdmissionError as exc:
            guard = str(exc)
        else:
            raise AssertionError('Native stock account gate did not reject')
        init.assert_not_called()
    revalidation = json.loads((HERE / 'technical_revalidation.json').read_text())
    for path, digest in json.loads(Path(revalidation['current_implementation_freeze']).read_text()).items():
        assert sha(path) == digest, path
    snapshots = json.loads((HERE / 'frozen_v1/manifest.json').read_text())
    for rec in snapshots:
        assert sha(rec['snapshot']) == rec['sha256']
    # Actual FA owners and inputs are unchanged, even though schedule admission
    # was strengthened and exactly revalidated independently.
    for path, digest in json.loads((HERE / 'predictive/owners.json').read_text()).items():
        assert sha(path) == digest, path
    parent = ROOT / 'research/equity/native_session_admission_20261114_v1/completion_receipt.json'
    prior = json.loads(parent.read_text())
    for path, digest in prior['source_and_artifacts'].items():
        assert sha(path) == digest, path
    sources = json.loads((HERE / 'inputs.json').read_text())
    for rec in sources['sources']:
        assert sha(rec['path']) == rec['sha256']
    inputs_verified = 0
    for rec in sources['records']:
        assert sha(rec['source']) == rec['source_sha256']
        assert sha(rec['staged']) == rec['staged_sha256']
        inputs_verified += 1
    result = dict(checked_utc=datetime.now(UTC).isoformat(), native=owner,
                  actual_runtime_owners=modules, stock_account_gate_before_native_init=True,
                  stock_account_gate_reason=guard, old_equity_receipt_unchanged=True,
                  old_equity_receipt_artifacts=len(prior['source_and_artifacts']),
                  input_source_and_staging_pairs_verified=inputs_verified,
                  source_documents_verified=len(sources['sources']),
                  exact_revalidation=revalidation, actual_funded_accounts=0,
                  return_metrics=None, source_paper_reproduced=False, passed=True)
    (HERE / 'verification.json').write_text(json.dumps(result, indent=2)+'\n')
    print('VERIFIED native owners, current/v1 code, 514 inputs, previous receipt; 0 accounts')


if __name__ == '__main__':
    main()
