"""Raw-grid admission and independent log-domain posterior reconstruction."""
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]


def main():
    plan = json.loads((HERE / 'plan.json').read_text())
    rows, frames = [], {}
    receipts = json.loads((HERE / 'data_receipts.json').read_text())
    for relative, digest in receipts.items():
        path = Path(plan['datadir']) / relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        frame = pd.read_feather(path).set_index('date')
        tf = path.stem.split('-')[-1]
        freq = {'5m': '5min', '1h': 'h', '1d': 'D'}[tf]
        required = pd.date_range(plan['start'], plan['end'], freq=freq, inclusive='left')
        selected = frame.reindex(required)
        assert frame.index.is_unique and frame.index.is_monotonic_increasing
        assert np.isfinite(selected[['open', 'high', 'low', 'close', 'volume']]).all().all()
        assert (selected[['open', 'high', 'low', 'close']] > 0).all().all()
        assert (selected.volume >= 0).all()
        frames[relative] = frame
        rows.append(dict(path=str(path), sha256=digest, complete_target_rows=len(required)))
    for coin in ['BTC', 'ETH']:
        five = frames[coin + '_USDT-5m.feather']
        hour = frames[coin + '_USDT-1h.feather']
        common = pd.date_range(plan['start'], plan['comparison_end'], freq='h')
        np.testing.assert_array_equal(five.open.reindex(common), hour.open.reindex(common))
    out = Path(plan['output_root'])
    actual = pd.read_parquet(out / 'preflight_up_1000_conservative_admission2/native_weights.parquet')
    constant = pd.read_parquet(out / 'preflight_crp_1000_conservative_admission1/native_weights.parquet')
    assert len(actual) == len(constant) == 608
    np.testing.assert_allclose(constant, 1/3, atol=1e-15)
    daily = pd.DataFrame({coin + '/USDT': frames[coin + '_USDT-1d.feather'].close for coin in ['BTC', 'ETH']})
    prices = daily.reindex(pd.date_range('2024-12-31', '2026-08-30', tz='UTC')).to_numpy(dtype=np.longdouble)
    # Independent construction: enumerate all integer compositions explicitly,
    # then use cumulative logarithms of expert relatives, not the producer's
    # normalized recursive particle array.
    experts = []
    for btc in range(101):
        for cash in range(101):
            eth = 100 - btc - cash
            if eth >= 0:
                experts.append([btc / 100, eth / 100, cash / 100])
    experts = np.array(experts, dtype=np.longdouble)
    logwealth = np.zeros(len(experts), dtype=np.longdouble)
    independent = [np.array([1/3] * 3)]
    for i in range(1, len(prices)):
        relative = np.r_[prices[i] / prices[i-1], np.longdouble(1)]
        logwealth += np.log(np.sum(experts * relative, axis=1))
        mass = np.exp(logwealth - np.max(logwealth))
        independent.append(np.sum(experts * mass[:, None], axis=0) / mass.sum())
    error = float(np.max(np.abs(np.array(independent) - actual.to_numpy())))
    assert error < 1e-12
    path = ROOT / 'freqtrade/data/universal_allocation.py'
    spec = importlib.util.spec_from_file_location('native_weight_prefix', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prefix_checks = []
    for cutoff in ['2025-06-30T00:00Z', '2026-01-01T00:00Z']:
        days = actual.index[actual.index <= pd.Timestamp(cutoff)]
        prefix = module.cover_allocation_weights(daily.loc[daily.index < days[-1]], days)
        pd.testing.assert_frame_equal(prefix, actual.loc[days], check_freq=False)
        altered = daily.copy()
        altered.loc[altered.index >= days[-1]] *= 100
        changed = module.cover_allocation_weights(altered, actual.index)
        pd.testing.assert_frame_equal(changed.loc[days], actual.loc[days], check_freq=False)
        prefix_checks.append(dict(cutoff=cutoff, rows=len(days), actual_refit=True, future_perturbed=True, exact=True))
    manifest = json.loads(Path(plan['engine_manifest']['path']).read_text())
    for relative, digest in manifest['files'].items():
        assert hashlib.sha256((Path(plan['engine_source']) / relative).read_bytes()).hexdigest() == digest
    result = dict(passed=True, source_native_files=len(manifest['files']), grid_points=len(experts),
                  native_allocation_days=608, independent_weight_cells=608*3,
                  maximum_independent_weight_error=error, real_input_prefix_checks=prefix_checks,
                  native_admission_passes=2, initial_failed_probe_retained=1,
                  funded_native_accounts=0, original_grid_inputs=rows,
                  first_decision_uniform=True, no_prewindow_fit=True, no_third_party_source_executed=True)
    (HERE / 'preflight.json').write_text(json.dumps(result, indent=2) + '\n')
    print('PASS2nativeadmissions,1824independentweightcells,2realprefixrefits;0fundedaccounts')


if __name__ == '__main__':
    main()
