"""Independent full-clock replay of actual native shifted flags, without money.

    This verifies nominal signal transport only. No positions, cash or funding
    are invented for an actual backtest, and no native funded fill is claimed.
"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def target(row, alpha):
    if row is None or row[f'state_{alpha}'] == 0:
        return {}
    sign = int(row[f'state_{alpha}'])
    return {row.symbol1 + '/USDT:USDT': -sign, row.symbol2 + '/USDT:USDT': sign}


def replay(flags, forecasts, alpha):
    groups = list(flags.groupby('at', sort=True))
    cursor, nominal, previous, checks, weekly = 0, {}, None, 0, 0

    def apply(until):
        nonlocal cursor
        while cursor < len(groups) and groups[cursor][0] <= until:
            when, part = groups[cursor]
            assert when.minute in [5, 10] and when.second == 0
            for row in part.loc[part.action.eq('exit')].itertuples():
                assert nominal.get(row.pair) == row.direction
                del nominal[row.pair]
            for row in part.loc[part.action.eq('entry')].itertuples():
                assert row.pair not in nominal
                nominal[row.pair] = row.direction
            assert len(nominal) in [0, 2]
            assert sum(nominal.values()) == 0
            cursor += 1

    for clock, row in forecasts.iterrows():
        apply(clock)
        assert nominal == target(previous, alpha), (clock, alpha, nominal, target(previous, alpha))
        checks += 1
        if previous is not None and row.week != previous.week:
            apply(clock + pd.Timedelta(minutes=5))
            assert not nominal, ('Missing weekly cash gap', clock, nominal)
            apply(clock + pd.Timedelta(minutes=10))
            assert nominal == target(row, alpha)
            weekly += 2
        previous = row
    apply(forecasts.index[-1] + pd.Timedelta(minutes=55))
    assert cursor == len(groups) and nominal == target(previous, alpha)
    return dict(common_hourly_nominal_state_checks=checks, weekly_gap_and_entry_checks=weekly,
                actual_native_leg_flags=len(flags), terminal_nominal_directions=nominal,
                actual_funded_positions_verified=False)


def main():
    pre = json.loads((HERE / 'native_preflight_flag_capture.json').read_text())
    capsule = json.loads((ROOT / 'user_data/crypto_quant/copula_account_native_20261115_v2/model_capsule.json').read_text())
    rec = capsule['forecasts']
    assert sha(rec['path']) == rec['sha256']
    forecasts = pd.read_parquet(rec['path'])
    outputs = []
    for result in pre['records']:
        record = result['native_leg_flags']
        assert sha(record['path']) == record['sha256']
        flags = pd.read_parquet(record['path'])
        outputs.append(dict(alpha=result['alpha'], **replay(flags, forecasts, result['alpha'])))
    # Additional exact transport-causality checks; existing model-causality
    # reconstruction belongs to sealed v2 and is not counted again here.
    path = ROOT / 'freqtrade/data/copula_execution.py'
    spec = importlib.util.spec_from_file_location('copula_execution_prefix_audit', path)
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    prefixes = []
    for alpha in [10, 15, 20]:
        full = owner.intent_schedule(forecasts, alpha)
        for value in ['2025-01-10T00:00Z', '2025-05-16T00:00Z', '2026-08-28T00:00Z']:
            cutoff = pd.Timestamp(value)
            expected = [r for r in full if r['decision_time'] < cutoff]
            truncated = owner.intent_schedule(forecasts.loc[forecasts.index < cutoff], alpha)
            assert expected == truncated
            changed = forecasts.copy()
            changed.loc[changed.index >= cutoff, f'state_{alpha}'] = 0
            altered = [r for r in owner.intent_schedule(changed, alpha) if r['decision_time'] < cutoff]
            assert expected == altered
            prefixes.append(dict(alpha=alpha, cutoff=value, exact_truncation=True, exact_future_state_perturbation=True))
    result = dict(passed=True, views=outputs,
        common_hourly_nominal_checks=sum(r['common_hourly_nominal_state_checks'] for r in outputs),
        weekly_boundary_checks=sum(r['weekly_gap_and_entry_checks'] for r in outputs),
        prefixes=prefixes, actual_transport_prefix_recalculations=18,
        native_funded_accounts=0, new_models=0, financial_returns='UNKNOWN',
        independent_direction_replay_imports_candidate_calculator=False)
    (HERE / 'native_intent_verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print('VERIFIED', result['common_hourly_nominal_checks'], 'nominal hourly states;', result['weekly_boundary_checks'], 'weekly boundaries;18transport-prefix checks;0fundedaccounts')


if __name__ == '__main__':
    main()
