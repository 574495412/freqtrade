"""Read-only EPS/source diagnostics; no factor or external account execution."""
import ast
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(name, value):
    (HERE / name).write_text(json.dumps(value, indent=2, default=str, allow_nan=False) + '\n')


def main():
    previous = ROOT / 'research/equity/high_recency_native_20261114_v1/inputs.json'
    records = json.loads(previous.read_text())['records']
    columns = []
    required = {'eps_estimate', 'eps_actual', 'estimate_observed_at', 'actual_observed_at',
                'fiscal_end', 'eps_basis', 'original_document_id'}
    for row in records:
        assert sha(row['source']) == row['source_sha256']
        assert sha(row['staged']) == row['staged_sha256']
        header = pd.read_csv(row['source'], nrows=0).columns.tolist()
        columns.append(dict(symbol=row['symbol'], source_sha256=row['source_sha256'],
                            available_fields=sorted(required.intersection(header)),
                            missing_fields=sorted(required.difference(header))))
    assert len(columns) == 514
    sample = HERE / 'sources/data/sample/sec'
    eps = pd.read_parquet(sample / 'quarterly_eps.parquet')
    events = pd.read_parquet(sample / 'announcements.parquet')
    # Isolate only two reviewed, side-effect-free source kernels. No imports,
    # network, package initialization, CLI or external portfolio engine runs.
    namespace = {'pd': pd, 'np': np, 'config': type('Defaults', (), {'SUE_HISTORY_QUARTERS': 8}),
                 'SEASONAL_LAG_DAYS': (330, 400),
                 'MIN_ANNOUNCEMENT_LAG_DAYS': 1, 'MAX_ANNOUNCEMENT_LAG_DAYS': 100}
    for file, function in [('surprise.py', 'standardized_unexpected_earnings'),
                           ('pipeline.py', 'match_announcements')]:
        source = HERE / 'sources/src/esa' / file
        tree = ast.parse(source.read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function)
        isolated = ast.Module(body=[node], type_ignores=[])
        exec(compile(isolated, str(source), 'exec'), namespace)
    sue = namespace['standardized_unexpected_earnings'](eps)
    matched = namespace['match_announcements'](sue, events)
    event_days = matched.accepted_utc.dt.tz_convert('America/New_York').dt.tz_localize(None).dt.normalize()
    history_pass = matched.history_known_by.notna() & matched.history_known_by.lt(event_days)
    finite = matched.sue.notna()
    later = matched.filed.gt(event_days)
    witnesses = matched.loc[history_pass & finite & later,
        ['ticker', 'end', 'filed', 'accepted_utc', 'accession', 'derived', 'history_known_by']]
    witnesses.to_csv(HERE / 'source_current_eps_clock_witnesses.csv', index=False)
    # These are input-stage candidates only, before the source's price,
    # membership, complete-return or study-window filters. No source-account
    # performance, alpha or look-ahead magnitude is inferred from this count.
    save('input_audit.json', dict(
        original_input_manifest=str(previous), original_input_manifest_sha256=sha(previous),
        source_and_staged_files_verified=514, input_files_with_complete_consensus_schema=sum(
            not r['missing_fields'] for r in columns), records=columns,
        source_sample=dict(eps_rows=len(eps), announcement_rows=len(events),
            sample_tickers=int(eps.ticker.nunique()), matched_quarters=len(matched),
            finite_sue_and_prior_history_guard=int((history_pass & finite).sum()),
            among_these_current_eps_filed_after_matched_release_day=int((history_pass & finite & later).sum()),
            original_actual_value_bound_to_release_exhibit=False,
            filtered_source_portfolio_or_result_executed=False,
            source_sample_is_target_universe=False),
        missing_data_is_zero=False, admitted_consensus_events=0,
        registry_factors=0, factor_analysis_calls=0, financial_accounts=0, bench_records=0))
    print('Verified514source/stagedinputs; consensus-schema0;', len(witnesses),
          'source sample current-EPS clock witnesses; not funded results')


if __name__ == '__main__':
    main()
