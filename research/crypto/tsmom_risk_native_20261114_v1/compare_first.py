"""Full-window native financial parity of callback scheduling, not a new alpha."""
import gzip
import json
from pathlib import Path

import pandas as pd

from research.crypto.band_trend_native_20261113_v1.analyze import read_native
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha

HERE = Path(__file__).resolve().parent


def main():
    original = json.loads((HERE/'plan.json').read_text())
    updated = json.loads((HERE/'v002/plan.json').read_text())
    name = 'CovarianceRiskTS_1000_15bps'
    left, right = Path(original['output_root'])/name, Path(updated['output_root'])/name
    for folder in [left,right]:
        receipt = json.loads((folder/'process_receipt.json').read_text())
        assert receipt['returncode'] == 0 and sha(folder/'result.json') == receipt['result_sha256']
    a, azip, _ = read_native(left, 'CovarianceRiskTS')
    b, bzip, _ = read_native(right, 'ClockedCovarianceRiskTS')
    keys = ['pair','is_short','open_timestamp','close_timestamp','amount','orders',
            'funding_fees','profit_abs','leverage','exit_reason']
    assert [{k:t[k] for k in keys} for t in a['trades']] == [{k:t[k] for k in keys} for t in b['trades']]
    assert a['profit_total_abs'] == b['profit_total_abs']
    assert a['final_balance'] == b['final_balance']
    for name in ['hourly_equity.parquet','native_weights.parquet','native_features.parquet']:
        pd.testing.assert_frame_equal(pd.read_parquet(left/name), pd.read_parquet(right/name),
                                      check_exact=True, check_dtype=False)
    with gzip.open(left/'strategy_trace.json.gz','rt') as f:
        old_trace = json.load(f)
    with gzip.open(right/'strategy_trace.json.gz','rt') as f:
        new_trace = json.load(f)
    assert old_trace == new_trace
    before = json.loads((left/'result.json').read_text())['elapsed_seconds']
    after = json.loads((right/'result.json').read_text())['elapsed_seconds']
    save(HERE/'v002/full_native_clock_parity.json', dict(passed=True,
        scope='Same complete account before and after native callback scheduling optimization',
        original_archive={'path':str(azip),'sha256':sha(azip)},
        clocked_archive={'path':str(bzip),'sha256':sha(bzip)},
        identical_native_trades=len(a['trades']), identical_native_orders=sum(len(t['orders']) for t in a['trades']),
        identical_hourly_nav_points=len(pd.read_parquet(left/'hourly_equity.parquet')),
        exact_daily_weights=True, exact_strategy_trace=True, exact_funding_and_terminal_cash=True,
        original_elapsed_seconds=before, clocked_elapsed_seconds=after,
        source_risk_signal_cost_pool_and_window_unchanged=True,
        additional_independent_alpha_accounts=0))
    print('FULL NATIVE PARITY', len(a['trades']), 'trades; all orders/NAV/funding exact', flush=True)


if __name__ == '__main__':
    main()
