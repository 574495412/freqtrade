"""Actual native strategy and independent allocation arithmetic before finance."""
import copy
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'user_data/crypto_quant/tsmom_risk_native_20261114_v1'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, default=str)+'\n')


def scalar_weights(close, days, mode):
    prices = close.to_numpy()
    returns = np.zeros(prices.shape)
    returns[1:] = prices[1:] / prices[:-1] - 1
    expected = []
    for day in days:
        pos = close.index.get_loc(day-pd.Timedelta(days=2))
        sig = np.array([sum(np.sign(prices[pos,j]/prices[pos-k,j]-1)
                            for k in [30,90,365])/3 for j in range(prices.shape[1])])
        vol = np.array([statistics.stdev(returns[pos-29:pos+1,j].tolist())*math.sqrt(365)
                        for j in range(prices.shape[1])])
        sample = returns[max(0,pos-1095):pos]
        centered = sample - sample.mean(axis=0)
        cov = centered.T @ centered / (len(sample)-1) * 365
        if mode == 'covariance':
            cov = .95*cov + .05*np.diag(np.diag(cov))
        elif mode == 'diagonal':
            cov = np.diag(np.diag(cov))
        z = sig/vol
        variance = float(z @ cov @ z)
        w = z/np.abs(z).sum() if mode == 'gross_one' else z*.2/math.sqrt(variance)
        w = np.clip(w, -.5, .5)
        if np.abs(w).sum() > 3:
            w *= 3/np.abs(w).sum()
        expected.append(w)
    return np.array(expected)


def main():
    if (HERE/'preflight.json').exists():
        raise FileExistsError('Preserve completed preflight')
    admission = json.loads((HERE/'input_admission.json').read_text())
    protocol = json.loads((HERE/'protocol.json').read_text())
    prior = json.loads((HERE.parent/'spread_liquidity_native_20261114_v1/plan.json').read_text())
    engine = Path(prior['engine_source'])
    manifest = json.loads(Path(prior['engine_manifest']['path']).read_text())
    assert sha(prior['engine_manifest']['path']) == prior['engine_manifest']['sha256']
    for name, digest in manifest['files'].items():
        assert sha(engine/name) == digest, name
    for rec in json.loads((HERE/'source_manifest.json').read_text())['files']:
        assert sha(rec['path']) == rec['sha256']
    for name, digest in json.loads(Path(admission['data_receipts']['path']).read_text()).items():
        assert sha(Path(admission['datadir'])/name) == digest, name
    sys.path.insert(0, str(engine))
    sys.path.insert(0, str(HERE))
    import freqtrade
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.data.history import load_pair_history
    from freqtrade.enums import CandleType, RunMode
    from freqtrade.resolvers import ExchangeResolver, StrategyResolver
    from RiskWeights import plan_weights
    assert Path(freqtrade.__file__).resolve() == engine/'freqtrade/__init__.py'
    pairs = protocol['local_pool']
    daily = {p: load_pair_history(p, '1d', Path(admission['datadir']), fill_up_missing=False,
                                  candle_type=CandleType.FUTURES).set_index('date').close for p in pairs}
    begin = max(s.index[0] for s in daily.values())
    close = pd.DataFrame(daily).reindex(pd.date_range(begin, '2026-08-31T00:00Z', freq='D'))
    days = pd.date_range('2025-01-01T00:00Z', '2026-08-31T00:00Z', freq='D')
    snapshot = json.loads(Path(admission['market_snapshot']['path']).read_text())
    templates, checks = {}, []
    for strategy, mode in [('CovarianceRiskTS','covariance'), ('DiagonalRiskTS','diagonal'),
                           ('GrossOneRiskTS','gross_one')]:
        raw = json.loads(Path(prior['candidates'][0]['config']['path']).read_text())
        raw = {k:v for k,v in raw.items() if not k.startswith('research_')}
        raw.update(strategy=strategy, dry_run_wallet=1000, margin_mode='cross', max_open_trades=29,
                   position_adjustment_enable=True, max_entry_position_adjustment=-1,
                   timeframe='1h', startup_candle_count=72)
        raw['exchange']['pair_whitelist'] = pairs
        templates[strategy] = copy.deepcopy(raw)
        config = copy.deepcopy(raw)
        config.update(strategy_path=str(HERE), user_data_dir=OUT, datadir=Path(admission['datadir']),
                      runmode=RunMode.BACKTEST, candle_type_def=CandleType.FUTURES,
                      timerange='1735689600-1788220799')
        owner = StrategyResolver.load_strategy(config)
        exchange = ExchangeResolver.load_exchange(config, validate=False, load_leverage_tiers=False)
        try:
            exchange._markets = snapshot['markets']
            exchange._leverage_tiers = snapshot['leverage_tiers']
            exchange._api.set_markets(snapshot['markets'], snapshot['currencies'])
            exchange._api_async.set_markets(snapshot['markets'], snapshot['currencies'])
            exchange.validate_config(config)
            for pair in pairs:
                assert exchange.get_max_leverage(pair, 500.) >= 3.
            owner.dp = DataProvider(config, exchange)
            owner.bot_start()
            expected = scalar_weights(close, days, mode)
            maximum = float(np.max(np.abs(owner.research_weights.to_numpy()-expected)))
            if maximum > 1e-10:
                raise ValueError('Independent source allocation mismatch')
            for cutoff in pd.to_datetime(['2025-07-01','2026-01-01','2026-08-01'], utc=True):
                before = days <= cutoff
                changed = close.copy()
                changed.loc[changed.index >= cutoff-pd.Timedelta(days=1)] *= 7
                altered = plan_weights(changed, days[before], mode)[0]
                truncated = plan_weights(close.loc[:cutoff-pd.Timedelta(days=2)], days[before], mode)[0]
                pd.testing.assert_frame_equal(altered, owner.research_weights.loc[days[before]], check_exact=True)
                pd.testing.assert_frame_equal(truncated, altered, check_exact=True)
            hours_checked = 0
            for pair in pairs:
                hourly = owner.dp.get_pair_dataframe(pair,'1h','futures')
                advised = owner.advise_exit(owner.advise_entry(owner.advise_indicators(
                    hourly.copy(), {'pair':pair}), {'pair':pair}), {'pair':pair})
                columns = ['enter_long','enter_short','exit_long','exit_short']
                assert not advised.loc[advised.date.dt.hour.ne(0), columns].ne(0).any().any()
                finite = advised.loc[advised.date.between(days[0], days[-1]+pd.Timedelta(hours=23)),
                                     ['target_weight','source_signal','source_volatility']]
                assert np.isfinite(finite).all().all()
                hours_checked += len(finite)
            owner.research_weights.to_parquet(OUT/f'preflight_weights_{strategy}.parquet')
            save(OUT/f'preflight_allocation_{strategy}.json', owner.research_allocation_diagnostics)
            checks.append(dict(strategy=strategy, mode=mode, scalar_weight_cells=expected.size,
                               maximum_weight_error=maximum, native_hourly_rows=hours_checked,
                               actual_prefix_refits=6,
                               minimum_covariance_observations=min(d['covariance_observations'] for d in owner.research_allocation_diagnostics)))
            print('PREFLIGHT', strategy, maximum, 'native hours', hours_checked, flush=True)
        finally:
            exchange.close()
    save(HERE/'config_templates.json', templates)
    sources = ['TSMomPortfolio.py','RiskWeights.py','AuthorMath.py','NativeAllocations.py',
               'preflight.py','test_tsmom.py','complete_inputs.py','native_runner.py','run.py']
    save(HERE/'preflight.json', dict(passed=True, strategy_type='fixed source strategy/allocation study, not new factor selection',
         native_commit=manifest['commit'], native_core_files_checked=len(manifest['files']),
         joint_daily_start=begin, observed_daily_rows=len(close), decisions=len(days),
         full_1095_day_covariance_at_first_decision=False, actual_strategies=checks,
         sources={str(HERE/n):sha(HERE/n) for n in sources},
         native_funded_accounts=0, supplied_funding_not_actual_settlement=True))


if __name__ == '__main__':
    main()
