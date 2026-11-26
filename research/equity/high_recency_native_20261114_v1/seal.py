"""Seal this research milestone; explicitly retain the unmet account objective."""
import json
from datetime import UTC, datetime

from bootstrap import HERE, ROOT, sha


def main():
    verification = json.loads((HERE / 'verification.json').read_text())
    diagnostics = json.loads((HERE / 'predictive_diagnostics.json').read_text())
    if not verification['passed'] or diagnostics['actual_factor_analysis_calls'] != 24:
        raise ValueError('Incomplete evidence')
    if '33 passed' not in (HERE / 'tests_v4.log').read_text():
        raise ValueError('Required current tests incomplete')
    paths = [p for p in HERE.rglob('*') if p.is_file()
             and '__pycache__' not in p.parts and p.suffix != '.pyc'
             and p.name != 'completion_receipt.json']
    paths += [ROOT / name for name in [
        'freqtrade/data/history/equity.py', 'freqtrade/data/equity_provider.py',
        'freqtrade/optimize/equity_backtesting.py',
        'tests/data/history/test_equity.py', 'tests/data/test_equity_provider.py',
        'tests/test_equity_runtime.py']]
    receipt = dict(
        sealed_utc=datetime.now(UTC).isoformat(),
        scope='HK/US native stock research data path and rejected quote-recency candidate',
        complete=False, research_milestone_verified=True,
        full_stock_account_implemented=False, funded_accounts=0,
        newly_accepted_independent_alphas=0, actual_factor_analysis_calls=24,
        numeric_diagnostics_are_funded_returns=False,
        higher_comparable_net_return_proved=False, financial_return_metrics=None,
        independent_oos_certified=False, tests_passed=33,
        preserved_failures=['Initial 3 test-fixture/datetime-unit failures',
                            'optuna-dependent unrelated conftest blocked test collection',
                            'first technical revalidation wrapper omitted bootstrap symlink'],
        native_core_files_checked=595, strategy_inputs_declared=514,
        full_warmup_inputs_admitted=486, rejected_inputs_retained=28,
        source_and_artifacts={str(p.resolve() if not p.is_symlink() else p): sha(p)
                              for p in sorted(set(paths))},
        production_enabled=False, orders=False, deployment=False,
        service_changes=False, commit=False, push=False,
        next_required_native_owner_work=[
            'Stock execution clocks in native Backtesting',
            'Stock order quantity/lot/tick and side-specific fee/tax records',
            'Spendable/unsettled cash in native Wallets and actual fill lifecycle',
            'Dividend entitlement/payment and corporate-action/security lifecycle',
            'Fixed same-capital/currency/window/cost benchmark and combination accounts'])
    (HERE / 'completion_receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    for path, digest in receipt['source_and_artifacts'].items():
        if sha(path) != digest:
            raise ValueError('Seal drift: ' + path)
    print('SEALED', len(receipt['source_and_artifacts']), 'artifacts; overall objective incomplete')


if __name__ == '__main__':
    main()
