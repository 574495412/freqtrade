"""Freeze native account matrix after actual strategy/input preflight."""
import json
from datetime import UTC, datetime
from pathlib import Path

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'user_data/crypto_quant/tsmom_risk_native_20261114_v1'


def ref(path):
    return {'path': str(path), 'sha256': sha(path)}


def main():
    if (HERE/'plan.json').exists():
        raise FileExistsError('Frozen native plan exists')
    preflight = json.loads((HERE/'preflight.json').read_text())
    if not preflight['passed'] or '11 passed' not in (HERE/'tests_v2.log').read_text():
        raise ValueError('Native preflight/tests incomplete')
    for path, digest in preflight['sources'].items():
        if sha(path) != digest:
            raise ValueError('Source changed since preflight: '+path)
    admission = json.loads((HERE/'input_admission.json').read_text())
    old = json.loads((HERE.parent/'spread_liquidity_native_20261114_v1/plan.json').read_text())
    templates = json.loads((HERE/'config_templates.json').read_text())
    candidates = []
    for strategy in ['CovarianceRiskTS','DiagonalRiskTS','GrossOneRiskTS']:
        for capital in [1000,500]:
            name = f'{strategy}_{capital}'
            config = templates[strategy]
            config['dry_run_wallet'] = capital
            path = HERE / (name+'.json')
            save(path, config)
            candidates.append(dict(name=name, strategy=strategy, capital=capital,
                                   pairs=admission['pairs'], config=ref(path),
                                   source_files={n:ref(HERE/n) for n in [
                                       'TSMomPortfolio.py','RiskWeights.py','AuthorMath.py','NativeAllocations.py']}))
    keys = ['engine_source','engine_manifest','engine_commit','environment','research_support',
            'storage_source','observer_source','start','end','comparison_end','periods',
            'memory_guard_bytes','wall_guard_seconds']
    plan = {k:old[k] for k in keys}
    seals = [HERE/n for n in ['protocol.json','source_manifest.json','input_admission.json',
                             'preflight.py','preflight.json','config_templates.json','run.py',
                             'test_tsmom.py','tests_v2.log','complete_inputs.py','register.py']]
    seals += [Path(r['path']) for r in json.loads((HERE/'source_manifest.json').read_text())['files']]
    seals += [ROOT/'research/crypto/band_trend_native_20261113_v1/observe_clock.py',
              ROOT/'research/crypto/skew_vol_native_20261113_v1/clock_fix.py']
    plan.update(schema='freqtrade.native.tsmom-risk-quantity.v1',
                registered_at=datetime.now(UTC).isoformat(),
                source_strategy_allocation_study=True, not_new_factor_screen=True,
                market_snapshot=admission['market_snapshot'], pairs=admission['pairs'],
                output_root=str(OUT), datadir=admission['datadir'],
                data_receipts=admission['data_receipts'],
                marks={p:ref(Path(admission['datadir'])/'futures'/
                             (p.split('/')[0]+'_USDT_USDT-1h-mark.feather')) for p in admission['pairs']},
                candidates=candidates, allowed_leverage=3., initial_equity_usdt=1000,
                total_cost_bps_per_side=[15,8], runner_sha256=sha(HERE/'native_runner.py'),
                runner_module='research.crypto.tsmom_risk_native_20261114_v1.native_runner',
                additional_source_seals=[ref(p) for p in seals],
                orders_enabled=False, production_eligible=False)
    save(HERE/'plan.json', plan)
    print('REGISTERED12 native accounts; no financial outcomes observed', flush=True)


if __name__ == '__main__':
    main()
