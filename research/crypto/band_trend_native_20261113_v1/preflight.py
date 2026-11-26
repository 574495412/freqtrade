"""Actual source causal-prefix and unchanged native contract preflight."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha, verify

HERE = Path(__file__).resolve().parent


def main():
    plan = json.loads((HERE / 'plan.json').read_text())
    runner = importlib.util.spec_from_file_location('band_native', HERE / 'native_runner.py')
    module = importlib.util.module_from_spec(runner)
    runner.loader.exec_module(module)
    rows = []
    for candidate in plan['candidates'][:]:
        module.verify(plan, candidate)
        if candidate['capital'] != 1000:
            continue
        strategy = candidate['strategy']
        source = Path(candidate['source_files'][strategy + '.py']['path'])
        spec = importlib.util.spec_from_file_location(strategy, source)
        owner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(owner)
        obj = getattr(owner, strategy)({'stake_currency': 'USDT', 'runmode': 'backtest'})
        # Hyperopt defaults here equal source defaults: neither source has buy_params/sell_params.
        for pair in [plan['pairs'][0], plan['pairs'][1], plan['pairs'][2]]:
            stem = pair.replace('/', '_').replace(':', '_')
            frame = pd.read_feather(Path(plan['datadir']) / 'futures' / f'{stem}-{obj.timeframe}-futures.feather')
            def calculate(data):
                result = obj.populate_indicators(data.copy(), {'pair': pair})
                result = obj.populate_entry_trend(result, {'pair': pair})
                return obj.populate_exit_trend(result, {'pair': pair})
            full = calculate(frame)
            for date in ['2025-01-01', '2026-01-01', '2026-08-01']:
                edge = pd.Timestamp(date, tz='UTC')
                prefix = frame.loc[frame.date <= edge].copy()
                actual = calculate(prefix)
                for key in ['enter_long', 'exit_long']:
                    if not full.loc[prefix.index, key].fillna(0).equals(actual[key].fillna(0)):
                        raise ValueError('Source signal prefix mismatch')
                modified = frame.copy()
                future = modified.date > edge
                modified.loc[future, ['open', 'high', 'low', 'close']] *= 7.0
                perturbed = calculate(modified)
                for key in ['enter_long', 'exit_long']:
                    if not full.loc[prefix.index, key].fillna(0).equals(perturbed.loc[prefix.index, key].fillna(0)):
                        raise ValueError('Future perturbation changes signal prefix')
                rows.append({'strategy': strategy, 'pair': pair, 'cutoff': str(edge), 'prefix_rows': len(prefix), 'passed': True})
    save(HERE / 'preflight.json', {'passed': True, 'actual_source_checks': rows,
                                 'source_hashes': {p.name: sha(p) for p in (HERE / 'sources').iterdir() if p.is_file()},
                                 'scope': 'Finite sampled source entry/exit prefixes; not full recursive or official lookahead certification.',
                                 'source_warmup_caveat': 'Average source startup0 retained; not recursive-converged certification.'})
    print(json.dumps({'passed': True, 'prefix_and_future_checks': len(rows)}))


if __name__ == '__main__':
    main()
