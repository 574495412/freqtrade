"""Rebuild every saved spot NAV from fills; no strategy or financial rerun."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE = HERE.parent / 'factor_cost_futures_20261106/reconstruct_account.py'
spec = importlib.util.spec_from_file_location('frozen_fill_reconstruction', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    records = []
    source_hashes = {str(p.relative_to(ROOT)): sha(p) for p in [Path(__file__), SOURCE]}
    for folder in ['factor_cost_scenario_20261106', 'factor_cost_long_20261106']:
        plan_path = HERE.parent / folder / 'plan.json'
        plan = read(plan_path)
        source_hashes[str(plan_path.relative_to(ROOT))] = sha(plan_path)
        for name, digest in plan['input_hashes'].items():
            assert sha(ROOT / name) == digest, name
        for row in plan['runs']:
            assert row['market'] == 'spot'
            directory = ROOT / 'user_data/crypto_quant/factor_cost_scenario_20261106' / row['id']
            assert read(directory / 'registration.json')['plan_sha256'] == sha(plan_path)
            for name, digest in read(directory / 'artifact_hashes.json').items():
                assert sha(directory / name) == digest, name
            result = read(directory / 'result.json')
            assert result['funding_including_native_terminal'] == 0
            curve = pd.read_parquet(directory / 'model_equity.parquet').set_index('date')
            assert curve.index.equals(pd.date_range(row['start'], row['end'], freq='5min', inclusive='left'))
            archives = list((directory / 'native_results').glob('*.zip'))
            assert len(archives) == 1
            with zipfile.ZipFile(archives[0]) as archive:
                names = [n for n in archive.namelist() if n.endswith('.json') and not n.endswith('_config.json')]
                assert len(names) == 1
                trades = json.loads(archive.read(names[0]))['strategy'][row['strategy']]['trades']
            assert all(not t['funding_fees'] for t in trades)
            receipt = read(ROOT / row['data_receipt'])
            prices = {}
            no_funding = {}
            for pair in {t['pair'] for t in trades}:
                name = pair.replace('/', '_') + '-5m.feather'
                path = ROOT / row['datadir'] / name
                assert sha(path) == receipt[name]
                prices[pair] = pd.read_feather(path).set_index('date').open
                no_funding[pair] = pd.Series([], index=pd.DatetimeIndex([], tz='UTC'), dtype=float)
            rebuilt = module.reconstruct(trades, curve.index, prices, no_funding)
            nav_error = float(np.abs(rebuilt['equity'] - curve.equity.to_numpy()).max())
            gross_error = float(np.abs(rebuilt['gross'] - curve.gross.to_numpy()).max())
            assert nav_error <= 1e-4 and gross_error <= 1e-4, (row['id'], nav_error, gross_error)
            assert np.count_nonzero(rebuilt['funding']) == 0
            records.append(dict(run_id=row['id'], window=row['window'], cost=row['cost'],
                observations=len(curve), all_5m_equity_maximum_error_usdt=nav_error,
                all_5m_gross_maximum_error_usdt=gross_error, passed=True,
                result_sha256=sha(directory / 'result.json'),
                artifact_manifest_sha256=sha(directory / 'artifact_hashes.json'),
                equity_sha256=sha(directory / 'model_equity.parquet'),
                artificial_terminal_exit_excluded=True, funding_usdt=0.,
                observed_model_mdd=float((1-curve.equity/curve.equity.cummax()).max()),
                rebuilt_model_mdd=float((1-rebuilt['equity']/np.maximum.accumulate(rebuilt['equity'])).max())))
            print('VERIFIED', row['id'], 'NAV error', nav_error, flush=True)
    assert len(records) == 12
    output = dict(observed_at_utc=datetime.now(timezone.utc).isoformat(), rows=records,
        account_count=len(records), five_minute_observations=sum(r['observations'] for r in records),
        source_hashes=source_hashes, new_financial_runs=0, new_causality_diagnostics=0,
        scope='Independent all-5m cash/inventory NAV reconstruction of saved spot fills with zero funding; same conditional OPEN and cost assumptions',
        historical_execution_certified=False, independent_oos=False, RD1_applied=False,
        production_eligible=False, orders_enabled=False)
    with (HERE / 'spot_curve_audit.json').open('x') as stream:
        json.dump(output, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


if __name__ == '__main__':
    main()
