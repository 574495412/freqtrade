"""Pinned-source causality and input availability, never funded return ranking.

Only two reviewed pure calculator method bodies are compiled. Repository
imports, scripts, network clients and pickle loaders are never executed.
"""
import ast
import hashlib
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'sources'


def save(name, value):
    (HERE / name).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def main():
    receipts = []
    for name in ['source_receipts.json', 'follow_source_receipts.json', 'data_source_receipts.json']:
        receipts.extend(json.loads((HERE / name).read_text()))
    for row in receipts:
        p = Path(row.get('path', SOURCE / row.get('name', '')))
        if 'sha256' in row:
            assert hashlib.sha256(p.read_bytes()).hexdigest() == row['sha256']
    tree = json.loads((SOURCE / 'fortune_tree.json').read_text())['tree']
    checked = []
    for path in ['data/southbound_holdings/southbound_long.csv', 'data/hkex_monthly_shares_history.csv']:
        raw = (SOURCE / ('fortune_' + path.replace('/', '_'))).read_bytes()
        entry = next(e for e in tree if e['path'] == path)
        digest = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        assert digest == entry['sha']
        checked.append(dict(path=path, bytes=len(raw), git_blob_sha1=digest))

    text = (SOURCE / 'fortune_ml_services_feature_engineering_behavioral_factors.py').read_text()
    module = ast.parse(text)
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == 'BehavioralFactorCalculator')
    selected = []
    bodies = []
    allowed = {'calculate_salience_factor', 'calculate_team_coin_factor'}
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name in allowed:
            assert not any(isinstance(n, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)) for n in ast.walk(node))
            assert not any(isinstance(n, ast.Name) and n.id in {'open', 'exec', 'eval', '__import__', 'os', 'sys', 'pickle', 'requests'} for n in ast.walk(node))
            selected.append(dict(method=node.name, start=node.lineno, end=node.end_lineno))
            node.decorator_list = []
            bodies.append(node)
    assert len(bodies) == 2
    context = dict(pd=pd, np=np, List=list, logger=logging.getLogger('reviewed_pure_source'))
    exec(compile(ast.Module(body=bodies, type_ignores=[]), '<reviewed pinned pure methods>', 'exec'), context)
    # No economic labels or account PnL. Append future observations to a known
    # synthetic prefix and compare the actual source methods on identical past.
    returns = .005 * np.sin(np.arange(120) * .7) * (1 + np.arange(120) / 80)
    frame = pd.DataFrame({'Close': 100 * np.exp(np.cumsum(returns))}, index=pd.bdate_range('2025-01-01', periods=120))
    frame.to_csv(HERE / 'source_causality_fixture.csv')
    prefix_results = []
    for name in sorted(allowed):
        fn = context[name]
        complete = fn(frame)
        for size in (40, 60, 80):
            prefix = fn(frame.iloc[:size])
            columns = [c for c in prefix if c != 'Close']
            for column in columns:
                left, right = prefix[column], complete[column].iloc[:size]
                valid = left.notna() & right.notna()
                delta = (left[valid] - right[valid]).abs()
                prefix_results.append(dict(method=name, prefix_rows=size, column=column,
                    finite_compared=int(valid.sum()), changed=int((delta > 1e-12).sum()),
                    max_abs_change=float(delta.max()) if len(delta) else None,
                    missing_mask_equal=bool(left.isna().equals(right.isna()))))
    leaking = [r for r in prefix_results if r['changed']]
    assert leaking and {r['column'] for r in leaking} == {'STR_Signal'}
    save('source_causality_audit.json', dict(passed=True, source_methods_reviewed=selected,
        checks=prefix_results, rejection='STR_Signal ranks over the complete per-security history before shift(1); future appends alter historical signals',
        financial_admission=False, code_adopted=False, registry_additions=0,
        factor_analysis_calls=0, accounts=0, economic_direction_tested=False,
        retained='Other component prefix invariance on this fixture does not prove alpha or full source correctness'))

    inputs_path = HERE.parent / 'high_recency_native_20261114_v1/inputs.json'
    inputs = json.loads(inputs_path.read_text())
    hk = [r['symbol'] for r in inputs['records'] if r['calendar'] == 'XHKG']
    schedule_record = inputs['schedules']['XHKG']
    schedule_path = Path(schedule_record['path'])
    assert hashlib.sha256(schedule_path.read_bytes()).hexdigest() == schedule_record['sha256']
    dates = pd.read_csv(schedule_path).iloc[:, 0].str[:10]
    target_dates = set(d for d in dates if '2025-01-01' <= d <= '2026-08-31')
    assert len(target_dates) == 408
    flow = pd.read_csv(SOURCE / 'fortune_data_southbound_holdings_southbound_long.csv')
    shares = pd.read_csv(SOURCE / 'fortune_data_hkex_monthly_shares_history.csv', dtype={'code': str})
    rows = []
    for symbol in hk:
        part = flow.loc[flow.pool_code == symbol]
        observed = set(part.date)
        matched = part.loc[part.date.isin(target_dates)]
        monthly = shares.loc[shares.code == symbol.split('.')[0].zfill(5)]
        rows.append(dict(symbol=symbol, source_rows=len(part), window_rows=len(matched),
            window_unique_dates=len(set(matched.date)), missing_target_sessions=len(target_dates - observed),
            duplicate_window_keys=int(matched.date.duplicated().sum()),
            missing_hold_shares=int(matched.hold_shares.isna().sum()),
            monthly_share_rows=len(monthly), historical_publication_clock_certified=False))
    manifest = json.loads((SOURCE / 'fortune_data_southbound_holdings_manifest.json').read_text())
    assert len(flow) == manifest['rows'] and flow.pool_code.nunique() == manifest['stocks']
    save('hk_flow_input_readiness.json', dict(source_pin='edeaf5535b9c60c70e1e52077414af3ec3b0b05e',
        original_hk_universe=hk, window=['2025-01-01', '2026-08-31'], target_sessions=408,
        verified_git_blobs=checked, source_manifest_counts_match=True,
        source_rows=len(flow), source_stocks=int(flow.pool_code.nunique()), records=rows,
        missing_handling='Unknown, never zero or previous observation carry',
        first_seen='Latest public Git snapshot and observation dates are not historical dissemination receipts',
        source_flow='Eastmoney per-stock holdings; not an authenticated exchange cash ledger',
        monthly_shares='Monthly report issued/treasury/total with filing date and source URL; no exact release time or complete corporate-action event contract',
        admitted_for_prediction=False, factor_analysis_calls=0, accounts=0))
    print('PASSED pinned source audit; STR_Signal future-dependent; HK flow input admission remains false')


if __name__ == '__main__':
    main()
