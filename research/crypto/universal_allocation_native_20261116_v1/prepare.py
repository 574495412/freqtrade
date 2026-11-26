"""Bind native source/config/data before any funded account is started."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]
PARENT = HERE.with_name('nfi_carry_matched_native_20261115_v1')


def record(path):
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def main():
    plan = json.loads((PARENT / 'plan.json').read_text())
    out = ROOT / 'user_data/crypto_quant' / HERE.name
    plan.update(registered_at=datetime.now(UTC).isoformat(), output_root=str(out),
                datadir=str(out / 'candles'), runner_module='research.crypto.' + HERE.name + '.native_runner',
                data_receipts=record(HERE / 'data_receipts.json'),
                runner_sha256=record(HERE / 'native_runner.py')['sha256'])
    module = ROOT / 'freqtrade/data/universal_allocation.py'
    helper = HERE / 'NativeAllocations.py'
    source_files = {n: record(p) for n, p in [('OnlineAllocation.py', HERE / 'OnlineAllocation.py'),
                                             ('UniversalSignals.py', module), ('NativeAllocations.py', helper)]}
    spot = next(c for c in plan['candidates'] if c['mode'] == 'spot')
    candidates = []
    for capital in [1000, 500]:
        for scenario, cost in [('conservative', 20), ('lower', 13)]:
            for label, strategy in [('up', 'OnlineAllocation'), ('crp', 'ConstantThirds')]:
                name = f'{label}_{capital}_{scenario}'
                config = json.loads(Path(spot['config']['path']).read_text())
                config.pop('grinding_enable', None)
                config.pop('nfi_parameters', None)
                config.update(dry_run_wallet=capital, strategy=strategy, timeframe='1h',
                              timeframe_detail='5m', fee=cost / 10000, position_adjustment_enable=True,
                              max_entry_position_adjustment=-1, stoploss=-.99, trailing_stop=False,
                              minimal_roi={}, research_universal_allocation=True,
                              allocation_start_day='2025-01-01T00:00:00Z',
                              allocation_end_day='2026-08-31T00:00:00Z', bot_name=name)
                config['order_types'] = {k: 'market' for k in ['entry', 'exit', 'emergency_exit', 'force_entry', 'force_exit', 'stoploss']}
                config['order_types']['stoploss_on_exchange'] = False
                config['exchange']['ccxt_config']['options']['fetchMarkets']['types'] = ['spot']
                config['exchange']['ccxt_async_config']['options']['fetchMarkets']['types'] = ['spot']
                path = HERE / (name + '.json')
                path.write_text(json.dumps(config, indent=2) + '\n')
                candidates.append(dict(name=name, strategy=strategy, capital=capital, mode='spot',
                                       scenario=scenario, cost_bps=cost, pairs=spot['pairs'],
                                       config=record(path), source_files=source_files,
                                       market_snapshot=spot['market_snapshot'], observer_source=spot['observer_source']))
    plan['candidates'] = candidates
    plan['pairs'] = spot['pairs']
    plan['total_cost_bps_per_side'] = [20, 13]
    plan['additional_source_seals'] = [record(HERE / n) for n in ['protocol.json', 'protocol_freeze.json',
                                      'OnlineAllocation.py', 'NativeAllocations.py', 'run.py', 'CaptureRules.py']]
    plan['additional_source_seals'] += [record(module), record(ROOT / 'research/crypto/band_trend_native_20261113_v1/observe_clock.py'),
                                        record(ROOT / 'research/crypto/skew_vol_native_20261113_v1/clock_fix.py')]
    (HERE / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    print('Registered8nativeconfigs;0financialstarts')


if __name__ == '__main__':
    main()
