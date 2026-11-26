"""Freeze exact upstream strategies and a matched native-account experiment."""
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil

import requests

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'user_data/crypto_quant/band_trend_native_20261113_v1'
PIN = 'f3340ce11f5bdf62f598522e64d1f5638eaa13f5'


def ref(path):
    return {'path': str(path), 'sha256': sha(path)}


def main():
    if (HERE / 'plan.json').exists() or OUT.exists():
        raise FileExistsError('Never overwrite a frozen campaign')
    OUT.mkdir()
    sources = HERE / 'sources'
    sources.mkdir()
    receipts = []
    for relative in ['user_data/strategies/Bandtastic.py', 'LICENSE']:
        url = f'https://raw.githubusercontent.com/freqtrade/freqtrade-strategies/{PIN}/{relative}'
        response = requests.get(url, timeout=30, proxies={'https': 'http://127.0.0.1:7897'})
        response.raise_for_status()
        target = sources / Path(relative).name
        target.write_bytes(response.content)
        receipts.append({'url': url, 'retrieved_at': datetime.now(UTC).isoformat(), **ref(target)})
    old = ROOT / 'research/crypto/official_factor_20261106/sources/AverageStrategy.py'
    shutil.copyfile(old, sources / old.name)
    receipts.append({'url': f'https://github.com/freqtrade/freqtrade-strategies/blob/{PIN}/user_data/strategies/berlinguyinca/AverageStrategy.py', 'local_predecessor': ref(old), **ref(sources / old.name)})
    save(HERE / 'source_receipts.json', receipts)
    parent = json.loads((ROOT / 'research/crypto/strategy_comparison_wide_20261105/plan.json').read_text())
    plan = {k: parent[k] for k in ['engine_source', 'engine_manifest', 'engine_commit', 'datadir', 'data_receipts', 'marks', 'market_snapshot', 'environment', 'research_support', 'storage_source', 'observer_source', 'pairs', 'periods', 'start', 'end', 'comparison_end']}
    # Both the single-strategy and 500-USDT sleeve accounts are real native wallets.
    # No scaling a 1,000-USDT curve to stand in for a 500-USDT execution path.
    base = json.loads((ROOT / 'research/crypto/official_factor_20261106/configs/futures_base.json').read_text())
    candidates = []
    for strategy in ['Bandtastic', 'AverageStrategy']:
        for capital in [1000, 500]:
            name = f'{strategy}_{capital}'
            config = {**base, 'strategy': strategy, 'dry_run_wallet': capital,
                      'timeframe_detail': '5m', 'export': 'trades'}
            config_path = HERE / f'{name}.json'
            save(config_path, config)
            candidates.append({'name': name, 'strategy': strategy, 'capital': capital,
                               'config': ref(config_path),
                               'source_files': {f'{strategy}.py': ref(sources / f'{strategy}.py')}})
    plan.update(schema='freqtrade.native.band-trend.v1', registered_at=datetime.now(UTC).isoformat(),
                initial_equity_usdt=1000, allowed_leverage=1.0, candidates=candidates,
                total_cost_bps_per_side=[15, 8], runner_sha256=sha(HERE / 'native_runner.py'),
                memory_guard_bytes=3 * 1024**3, wall_guard_seconds=3600,
                source_receipts=ref(HERE / 'source_receipts.json'),
                combination={'sleeves': ['Bandtastic_500', 'AverageStrategy_500'],
                             'initial_usdt': 1000, 'transfers': False, 'rebalance': False,
                             'policy': 'Sum simultaneous actual native sleeve NAVs; each sleeve max6 open positions, combined max12. Separate isolated wallets, not a shared-wallet portfolio.'},
                frozen_gate={'positive_at_both_costs': True, 'beat_both_single_strategy_returns_at_both_costs': True,
                             'maximum_hourly_mark_drawdown': 0.5, 'each_continuous_calendar_slice_positive': True},
                limitations=['Previously examined retrospective fixed40 history and current market rules; not independent OOS or PIT universe.',
                             'Long-only 1x perpetual strategies retain exact source signal/ROI/stop/trailing/warmup; futures funding charged natively.',
                             'Hourly mark OPEN NAV includes floating PnL, funding and close-cost reserve; not worst5m/intrabar DD.',
                             'Two separately funded wallets permit duplicate symbols; total margin cannot move between sleeves.',
                             'Bandtastic source embeds previously optimized defaults; no new hyperopt, no independence claim.',
                             'AverageStrategy source startup0 is retained; old recursive/warmup caveats remain.'],
                orders_enabled=False, production_eligible=False, no_post_result_retuning=True)
    save(HERE / 'plan.json', plan)
    print(json.dumps({'plan': str(HERE / 'plan.json'), 'accounts': 8, 'orders_enabled': False}))


if __name__ == '__main__':
    main()
