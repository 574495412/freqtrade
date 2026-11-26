"""Resume failed input preparation; preserve frozen protocol and all source rows."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'user_data/crypto_quant/tsmom_risk_native_20261114_v1'


def ref(path):
    return {'path': str(path), 'sha256': sha(path)}


def main():
    if (HERE / 'input_admission.json').exists():
        raise FileExistsError('Completed input admission already exists')
    protocol = json.loads((HERE / 'protocol.json').read_text())
    pairs = protocol['local_pool']
    parent = HERE.parent / 'funding_cross_section_native_20261114_v1/input_admission.json'
    prior = json.loads(parent.read_text())
    flow = json.loads((HERE.parent / 'taker_flow_native_20261114_v1/input_admission_v2.json').read_text())
    original = Path(prior['funding_events']['path']).parent / 'candles/futures'
    canonical = Path(flow['native_datadir']) / 'futures'
    receipts = json.loads((OUT / 'data_receipts.json').read_text())
    if len(receipts) != 145:
        raise ValueError('Incomplete existing preparation; no overwrite/rebuild')
    for name, digest in receipts.items():
        if sha(OUT / 'candles' / name) != digest:
            raise ValueError('Prepared input drift: ' + name)
    records = []
    for pair in pairs:
        symbol = pair.split('/')[0]
        for suffix, directory in [('1h-futures', canonical), ('5m-futures', canonical),
                                  ('1h-mark', original), ('1h-funding_rate', original)]:
            source = directory / f'{symbol}_USDT_USDT-{suffix}.feather'
            target = OUT / 'candles/futures' / source.name
            if sha(source) != sha(target):
                raise ValueError('Source link drift')
            records.append({'source': str(source), 'target': str(target), 'sha256': sha(target)})
        old = pd.read_feather(original / f'{symbol}_USDT_USDT-1d-futures.feather')
        hours = pd.read_feather(canonical / f'{symbol}_USDT_USDT-1h-futures.feather')
        daily = hours.set_index('date').resample('D').agg(
            {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
        ).reset_index()
        expected = pd.concat([old.loc[old.date < daily.date.iloc[0], daily.columns], daily], ignore_index=True)
        path = OUT / f'candles/futures/{symbol}_USDT_USDT-1d-futures.feather'
        actual = pd.read_feather(path)
        pd.testing.assert_frame_equal(expected, actual, check_exact=True)
        if not np.isfinite(actual[['open', 'high', 'low', 'close', 'volume']]).all().all():
            raise ValueError('Nonfinite daily splice')
        if not pd.DatetimeIndex(actual.date).as_unit('ns').equals(
            pd.date_range(actual.date.iloc[0], actual.date.iloc[-1], freq='D').as_unit('ns')
        ):
            raise ValueError('Incomplete daily splice')
        records.append({'target': str(path), 'sha256': sha(path),
                        'daily_splice': 'original before2024Dec1, canonical5m-derived thereafter'})
    source_plan = HERE.parent / 'nfi80_capacity_20261109/L3_S12-v003.json'
    source_ref = json.loads(source_plan.read_text())['original_full_market_snapshot']
    metadata = Path(source_ref['path'])
    if sha(metadata) != source_ref['sha256']:
        raise ValueError('Original full metadata drift')
    snapshot = json.loads(metadata.read_text())
    if any(p not in snapshot['markets'] or p not in snapshot['leverage_tiers'] for p in pairs):
        raise ValueError('Missing full-pool margin metadata')
    events = pd.read_parquet(prior['funding_events']['path'])
    if events.event_time.dt.hour.eq(1).any():
        raise ValueError('Daily rebalance overlaps a supplied funding event')
    filtered = ROOT / 'user_data/crypto_quant/nfi80_historical_universe_20261109_v001/market-rules.json'
    save(HERE / 'preparation_failure_v1.json', {
        'stage': 'margin metadata admission after all145files prepared',
        'reason': 'filtered80 snapshot lacks BNB; no financial account started',
        'failed_snapshot': ref(filtered), 'missing_pairs': ['BNB/USDT:USDT'],
        'resolution': 'Use original full snapshot already cited by frozen upstream research plan',
        'original_snapshot_provenance': ref(source_plan), 'source_pool_changed': False})
    save(HERE / 'input_admission.json', {
        'price_inputs_bound': True, 'financial_settlement_certified': False, 'pairs': pairs,
        'source_links': records, 'datadir': str(OUT / 'candles'),
        'data_receipts': ref(OUT / 'data_receipts.json'), 'market_snapshot': ref(metadata),
        'funding_limit': ref(parent), 'funding_events': prior['funding_events'],
        'protocol': ref(HERE / 'protocol.json'), 'old_data_changed': False,
        'frozen_metadata_historical_rule_certification': False,
        'supplied_funding_events_at_rebalance_hour01': 0})
    print('ADMITTED29/145 exact inputs; full original snapshot; no funding events at01', flush=True)


if __name__ == '__main__':
    main()
