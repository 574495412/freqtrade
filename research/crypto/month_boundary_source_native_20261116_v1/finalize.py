"""Verify full native evidence and post-result seal,never launch an account."""
import hashlib
import json
from datetime import datetime, UTC
from pathlib import Path

HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(name, value):
    (HERE / name).write_text(json.dumps(value, indent=2) + '\n')


def main():
    frozen = read(HERE / 'implementation_freeze.json')
    for path, digest in frozen.items():
        assert sha(path) == digest, path
    protocol = read(HERE / 'protocol_freeze.json')
    assert sha(protocol['path']) == protocol['sha256']
    assert len(frozen) == 31
    plan = read(HERE / 'plan.json')
    manifest = read(plan['engine_manifest']['path'])
    for path, digest in manifest['files'].items():
        assert sha(Path(plan['engine_source']) / path) == digest
    parent_rows = []
    for row in read(HERE / 'parent_seals_before.json'):
        assert sha(row['path']) == row['sha256']
        files = read(row['path'])['files']
        for file in files:
            assert sha(file['path']) == file['sha256'], file['path']
        parent_rows.append(dict(**row, checked_paths=len(files)))
    for row in read(HERE / 'source_receipts.json'):
        body = Path(row['path']).read_bytes()
        assert hashlib.sha256(body).hexdigest() == row['sha256']
        assert hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest() == row['git_blob']
    paper = read(HERE / 'original_article_receipt.json')
    assert sha(paper['path']) == paper['sha256']
    assert len(read(HERE / 'source_receipts.json')) == 10
    receipts, peaks = [], []
    for candidate in plan['candidates']:
        directory = Path(plan['output_root']) / (candidate['name'] + f"_{candidate['cost_bps']}bps")
        receipt = read(directory / 'process_receipt.json')
        assert receipt['returncode'] == 0 and sha(directory / 'result.json') == receipt['result_sha256']
        assert read(directory / 'independent_account_audit.json')['passed']
        peaks.append(read(directory / 'progress.json')['maximum_rss_bytes'] if (directory / 'progress.json').exists() else None)
        receipts.append(receipt)
    assert len(receipts) == 12 and max(p for p in peaks if p is not None) < plan['memory_guard_bytes']
    for previous, following in zip(receipts[:-1], receipts[1:], strict=True):
        assert datetime.fromisoformat(previous['finished_at']) <= datetime.fromisoformat(following['started_at'])
    first = datetime.fromisoformat(receipts[0]['started_at'])
    assert (HERE / 'implementation_freeze.json').stat().st_mtime < first.timestamp()
    assert datetime.fromisoformat(read(HERE / 'protocol.json')['frozen_at']) < first
    results = read(HERE / 'results.json')
    budgets = read(HERE / 'execution_audit.json')
    wallets = read(HERE / 'native_wallet_audit.json')
    assert budgets['passed'] and wallets['passed']
    assert budgets['actual_entry_budgets'] == 344 and budgets['actual_natural_closes'] == 320
    assert budgets['separate_force_exits'] == 24
    assert read(HERE / 'preflight.json')['native_converted_hour_rows'] == 87552
    assert read(HERE / 'preflight.json')['source_calendar_tests'] == 9
    assert wallets['actual_native_balance_rows'] == 330216
    assert results['new_native_wallets'] == 12 and results['new_simulated_fills'] == 688
    assert results['new_native_trades'] == 344 and results['new_hour_points'] == 175104
    assert len(results['standalone_gates']) == 8
    assert len(results['portfolios']) == 10 and not any(p['frozen_financial_gate'] for p in results['portfolios'])
    assert not any(p['frozen_financial_gate'] for p in results['standalone_gates'])
    components = read(HERE / 'endpoint_components.json')
    assert sum(r['common_endpoint_actual_fills'] for r in components) == 664
    assert sum(r['actual_force_exit_fills_excluded'] for r in components) == 24
    save('execution_review.json', dict(actual_native_processes=receipts, all_exit_zero=True,
         financial_attempts=12, financial_failures=0, financial_replays=0,
         rss_sampled_workers=sum(p is not None for p in peaks),
         short_workers_without_rss_sample=sum(p is None for p in peaks),
         complete_all_worker_peak_rss_verified=False,
         one_financial_child_at_a_time=True, maximum_rss_bytes=max(p for p in peaks if p is not None),
         first_native_start=first.isoformat(), implementation_freeze_file_mtime_utc=
         datetime.fromtimestamp((HERE / 'implementation_freeze.json').stat().st_mtime, UTC).isoformat()))
    save('verification.json', dict(passed=True, original_engine_files=len(manifest['files']),
         local_native_producer_freeze_paths=len(frozen), parent_seals=parent_rows,
         source_git_blobs=10, primary_original_article_sha256=paper['sha256'], actual_native_wallets=12,
         actual_native_trades=344, naturally_completed_trades=320, actual_simulated_fills=688,
         common_endpoint_fills=664, separate_terminal_force_exits=24,
         complete_hourly_nav_points=175104, maximum_independent_nav_error_usdt=
         results['maximum_independent_nav_error_usdt'], actual_native_wallet_balance_rows=330216,
         independent_native_cash_and_holdings_audit=True, actual_entry_budgets=344,
         independently_checked_entry_quantities=344, actual500plus500_portfolios=10,
         financial_gate_passes=0, new_accepted_independent_alpha=0, new_factor_analysis=0,
         new_vibe_bench=0, source_author_account_reproduced=False, independent_oos=False,
         full_history_venue_rules_certified=False, private_cash_or_funding_certified=False,
         production_eligible=False, orders_enabled=False, services_changed=False))
    save('goal_audit.json', dict(goal_complete=False, prior_goal_turn='progress:sealed matched native comparison and completed equity technical evidence;no crypto goal completion',
         current_progress='calendar-family mechanism tested through12actualnativewallets and10fixed actual500portfolios;both source variants and all portfolios rejected',
         source_learning='two pinned MIT GitHub definitions reviewed;original article ETP versus calendar citation gap retained;no new complete social rules',
         new_mechanism_candidate_economic_result='rejected', new_accepted_independent_alpha=0,
         higher_comparable_net_return_established=False, valid_new_portfolio=False,
         independent_validation=False, goal_status='active',
         no_parameter_rescue=True, no_live_orders_or_service_changes=True))
    excluded = {'artifact_manifest.json', 'checkpoint_receipt.json'}
    paths = {p for base in [HERE, Path(plan['output_root'])] for p in base.rglob('*')
             if p.is_file() and p.name not in excluded and '__pycache__' not in p.parts}
    paths.update([ROOT / 'freqtrade/data/month_boundary.py', ROOT / 'freqtrade/data/month_boundary.md'])
    rows = [dict(path=str(path), sha256=sha(path), bytes=path.stat().st_size) for path in sorted(paths)]
    save('artifact_manifest.json', dict(scope='post-result rejected native calendar study;not preregistration or independentOOS', files=rows))
    for row in rows:
        assert sha(row['path']) == row['sha256']
    save('checkpoint_receipt.json', dict(manifest_sha256=sha(HERE / 'artifact_manifest.json'),
         verified_paths=len(rows), actual_native_wallets=12, goal_complete=False))
    print(json.dumps(dict(files=len(rows), manifest_sha256=sha(HERE / 'artifact_manifest.json'),
                         maximum_rss_bytes=max(p for p in peaks if p is not None), accepted_alpha=0)))


if __name__ == '__main__':
    main()
