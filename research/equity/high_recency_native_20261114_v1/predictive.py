"""Vibe Registry -> actual factor_analysis, conditional quote diagnostics only."""

import hashlib
import importlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, '/Users/chrise/GitHub/Vibe-Trading/agent')
from src.factors.registry import Registry
from src.tools.factor_analysis_tool import FactorAnalysisTool


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def rank(values):
    order = sorted(range(len(values)), key=values.__getitem__)
    result = [0.] * len(values)
    i = 0
    while i < len(values):
        end = i + 1
        while end < len(values) and values[order[end]] == values[order[i]]:
            end += 1
        for j in range(i, end):
            result[order[j]] = (i + 1 + end) / 2
        i = end
    return result


def ic_value(left, right):
    x, y = rank(left), rank(right)
    xm, ym = math.fsum(x) / len(x), math.fsum(y) / len(y)
    denominator = math.sqrt(math.fsum((a - xm)**2 for a in x) * math.fsum((b - ym)**2 for b in y))
    return math.fsum((a - xm)*(b - ym) for a, b in zip(x, y, strict=True)) / denominator


def audit(folder):
    # Audit exactly what the owner parsed, including CSV floating-point precision.
    f = pd.read_csv(folder / 'factor.csv', index_col=0, parse_dates=True)
    y = pd.read_csv(folder / 'returns.csv', index_col=0, parse_dates=True)
    ic = pd.read_csv(folder / 'native/ic_series.csv', index_col=0, parse_dates=True).iloc[:, 0]
    group = pd.read_csv(folder / 'native/group_equity.csv', index_col=0, parse_dates=True)
    running = [1., 1., 1.]
    max_error = 0.
    for day, values in f.iterrows():
        valid = values.notna() & y.loc[day].notna()
        a, b = values[valid].tolist(), y.loc[day, valid].tolist()
        if len(a) >= 5 and len(set(a)) > 1 and len(set(b)) > 1:
            expected = ic_value(a, b)
            if day not in ic.index or abs(expected - ic.loc[day]) > 1e-12:
                raise ValueError('Independent IC audit failed')
        if len(a) < 3:
            continue
        order = sorted(range(len(a)), key=lambda j: (a[j], j))
        cuts = [(len(a)-1)*((g/3*100)/100) for g in [1, 2]]
        members = [[j for k, j in enumerate(order)
                    if (0 if k <= cuts[0] else 1 if k <= cuts[1] else 2) == g] for g in range(3)]
        for g in range(3):
            running[g] *= 1 + math.fsum(b[j] for j in members[g]) / len(members[g])
        max_error = max(max_error, max(abs(a-b) for a, b in zip(running, group.loc[day], strict=True)))
    if max_error > 1e-10:
        raise ValueError('Independent native group-NAV audit failed')
    return dict(ic_rows=len(ic), group_nav_cells=group.size, maximum_group_nav_error=max_error)


def main():
    inputs = json.loads((HERE / 'inputs.json').read_text())
    admission = json.loads((HERE / 'native_admission.json').read_text())
    if sha(admission['feature_path']) != admission['feature_sha256']:
        raise ValueError('Native exposure drift')
    for path, digest in json.loads((HERE / 'implementation_freeze.json').read_text()).items():
        if sha(path) != digest:
            raise ValueError('Native implementation drift')
    directory = HERE / 'predictive'
    directory.mkdir(exist_ok=False)
    zoo = directory / 'zoo/core'
    zoo.mkdir(parents=True)
    meta = dict(id='core_native_calendar_high_recency', nickname='Native quote-price high recency',
                theme=['momentum'], formula_latex='1 - days_since_latest_52week_high / 364',
                columns_required=['close'], extras_required=['native_recency'],
                universe=['equity_us', 'equity_hk'], frequency=['1d'], decay_horizon=21,
                min_warmup_bars=0, stage='research',
                notes='Isolated view of actual Freqtrade IStrategy output; quote-price local hypothesis, not corporate-action-adjusted source PEAD or a funded return.')
    (zoo / 'native_calendar_high_recency.py').write_text(
        '__alpha_meta__ = ' + repr(meta) + '\n\ndef compute(panel):\n    return panel["native_recency"].copy()\n')
    view = Registry(zoo_root=directory / 'zoo')
    owning_registry = Registry()
    native = pd.read_parquet(admission['feature_path'])
    results = []
    comparisons = []
    total_ic, total_group = 0, 0
    for market in ['XNYS', 'XHKG']:
        symbols = [r['pair'] for r in inputs['records'] if r['calendar'] == market]
        schedule = pd.read_csv(inputs['schedules'][market]['path'], index_col=0)
        days = pd.DatetimeIndex(pd.to_datetime(schedule.index, utc=True))
        source = native.loc[native.market.eq(market)]
        closes = source.pivot(index='date', columns='pair', values='close').reindex(index=days, columns=symbols)
        opens = source.pivot(index='date', columns='pair', values='open').reindex(index=days, columns=symbols)
        raw = source.pivot(index='date', columns='pair', values='calendar_high_recency').reindex(index=days, columns=symbols)
        values = view.compute('core_native_calendar_high_recency', {'close': closes, 'native_recency': raw})
        pd.testing.assert_frame_equal(values, raw, check_exact=True)
        # Existing actual Registry definition. The other two controls are explicitly
        # quote-price diagnostics, not the existing dividend-aware core momentum.
        high = owning_registry.compute('academic_high52w', {'close': closes})
        controls = dict(recency=values, academic_high52w=high,
                        quote_momentum_252_21=closes.shift(21) / closes.shift(252) - 1,
                        quote_reversal_5=1 - closes / closes.shift(5))
        monthly = {}
        for month in pd.period_range('2025-01', '2026-08', freq='M'):
            month_days = days[days.strftime('%Y-%m') == str(month)]
            if len(month_days):
                monthly[str(month)] = month_days[0]
        labels, observed, clocks = [], [], []
        for month in list(monthly)[:-1]:
            entry = monthly[month]
            next_month = str(pd.Period(month, freq='M') + 1)
            end = monthly[next_month]
            obs = days[days.get_loc(entry)-1]
            labels.append((opens.loc[end] / opens.loc[entry] - 1).rename(obs))
            observed.append(obs)
            clocks.append(dict(observed_session=obs.isoformat(),
                               observed_close=str(schedule.loc[obs.strftime('%Y-%m-%d'), 'close']),
                               entry_session=entry.isoformat(),
                               entry_open=str(schedule.loc[entry.strftime('%Y-%m-%d'), 'open']),
                               exit_session=end.isoformat(), label='price-only open/open'))
        returns = pd.DataFrame(labels)
        for name, control in list(controls.items())[1:]:
            corr = values.loc[observed].corrwith(control.loc[observed], axis=1)
            comparisons.append(dict(market=market, left='recency', right=name,
                dates=int(corr.notna().sum()), mean_abs_cross_sectional_pearson=float(corr.abs().mean()),
                duplicate=bool(corr.abs().mean() > .99)))
        folder = directory / market
        folder.mkdir()
        save(folder / 'label_clocks.json', clocks)
        for name, control in controls.items():
            factor = control.loc[observed]
            for split, subset in [('all', observed),
                                  ('2025', [d for d in observed if (d + pd.Timedelta(days=7)).year == 2025]),
                                  ('2026', [d for d in observed if (d + pd.Timedelta(days=7)).year == 2026])]:
                target = folder / name / split
                target.mkdir(parents=True)
                factor.loc[subset].to_csv(target / 'factor.csv')
                returns.loc[subset].to_csv(target / 'returns.csv')
                response = json.loads(FactorAnalysisTool().execute(
                    factor_csv=str(target / 'factor.csv'), return_csv=str(target / 'returns.csv'),
                    output_dir=str(target / 'native'), n_groups=3))
                save(target / 'native_result.json', response)
                if response['status'] != 'ok':
                    raise ValueError(response)
                checked = audit(target)
                total_ic += checked['ic_rows']
                total_group += checked['group_nav_cells']
                numeric = (response['ic_mean'] > .03 and response['ir'] > .5
                           and response['ic_positive_ratio'] > .55)
                support = int((factor.loc[subset].notna() & returns.loc[subset].notna()).sum(axis=1).min())
                results.append(dict(market=market, factor=name, split=split,
                                    response=response, independent_audit=checked,
                                    minimum_valid_assets=support, declared_assets=len(symbols),
                                    numeric_gate=numeric, minimum_observations_gate=len(subset) >= 24,
                                    source_adjustment_gate=False, admission_passed=False))
                print('FA', market, name, split, response['ic_mean'], response['ir'], flush=True)
    from src.factors import registry, factor_analysis_core
    from src.tools import factor_analysis_tool
    owners = [Path(registry.__file__), Path(factor_analysis_core.__file__),
              Path(factor_analysis_tool.__file__),
              Path(importlib.import_module(owning_registry.get('academic_high52w').module_path).__file__),
              Path(__file__), *zoo.glob('*.py')]
    save(directory / 'owners.json', {str(p): sha(p) for p in owners})
    save(HERE / 'predictive_diagnostics.json', dict(
        actual_factor_analysis_calls=len(results), isolated_registry_views=1,
        existing_registry_definition_used='academic_high52w', global_registry_modified=False,
        source_strategy_reproduced=False, source_prices_certified=False,
        full_original_universe_admitted=False, new_accepted_independent_alphas=0,
        native_funded_accounts=0, financial_net_return=None, independent_oos=False,
        independent_ic_rows=total_ic, independent_group_nav_cells=total_group,
        scoped_dedup=comparisons, all_registry_independence_claim=False, results=results))
    print('DONE', len(results), 'actual FA calls; 0 accepted alphas / funded accounts', flush=True)


if __name__ == '__main__':
    main()
