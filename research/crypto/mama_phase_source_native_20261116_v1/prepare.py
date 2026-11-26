"""Freeze shared-wallet native calendar configurations before economic results."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]
PARENT = HERE.with_name('month_boundary_source_native_20261116_v1')


def record(path):
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def main():
    plan = json.loads((PARENT / 'plan.json').read_text())
    template = json.loads(Path(plan['candidates'][0]['config']['path']).read_text())
    representative = plan['candidates'][0]
    out = ROOT / 'user_data/crypto_quant' / HERE.name
    plan.update(registered_at=datetime.now(UTC).isoformat(), output_root=str(out),
                datadir=str(out / 'candles'), native_start='2024-12-31T23:00:00Z',
                runner_module='research.crypto.' + HERE.name + '.native_runner',
                data_receipts=record(HERE / 'data_receipts.json'),
                runner_sha256=record(HERE / 'native_runner.py')['sha256'])
    signal = HERE / 'MAMAInputs.py'
    sources = {'PhaseMAMA.py': record(HERE / 'PhaseMAMA.py'), 'MAMAInputs.py': record(signal)}
    candidates = []
    for capital in [1000, 500]:
        for scenario, cost in [('conservative', 20), ('lower', 13)]:
            for label, strategy in [('mama', 'PhaseMAMA')]:
                config = json.loads(json.dumps(template))
                for key in ['research_universal_allocation', 'allocation_start_day', 'allocation_end_day', 'research_month_boundary']:
                    config.pop(key, None)
                name = f'{label}_{capital}_{scenario}'
                config.update(dry_run_wallet=capital, strategy=strategy, fee=cost / 10000,
                              position_adjustment_enable=False, max_entry_position_adjustment=0,
                              research_mama_phase=True, bot_name=name)
                path = HERE / (name + '.json')
                path.write_text(json.dumps(config, indent=2) + '\n')
                candidates.append(dict(name=name, strategy=strategy, capital=capital, mode='spot',
                                       scenario=scenario, cost_bps=cost, pairs=plan['pairs'], config=record(path),
                                       source_files=sources, market_snapshot=representative['market_snapshot'],
                                       observer_source=representative['observer_source']))
    plan['candidates'] = candidates
    plan['additional_source_seals'] = [record(HERE / name) for name in ['protocol.json', 'protocol_freeze.json',
        'PhaseMAMA.py', 'run.py', 'CaptureRules.py']]
    plan['additional_source_seals'] += [record(signal), record(ROOT / 'research/crypto/band_trend_native_20261113_v1/observe_clock.py'),
                                        record(ROOT / 'research/crypto/skew_vol_native_20261113_v1/clock_fix.py')]
    plan['additional_source_seals'] += [record(HERE / 'talib_environment.json'), record(Path(json.loads((HERE / 'talib_environment.json').read_text())['binary_path']))]
    (HERE / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    print('Registered4nativeconfigs;0financialstarts')


if __name__ == '__main__':
    main()
