"""Actual pinned history/DataProvider/IStrategy path; no financial simulation."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from bootstrap import HERE, ROOT, boot, sha


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def main():
    owner = boot()
    from freqtrade.data.equity_provider import EquityDataProvider, EquitySchedule
    from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
    from freqtrade.data.history.equity import EquityAdmissionError, EquitySessionContract
    from freqtrade.enums import CandleType, RunMode
    from freqtrade.resolvers import StrategyResolver

    frozen = json.loads((HERE / 'inputs.json').read_text())
    if sha(HERE / 'protocol.json') != frozen['protocol_sha256']:
        raise ValueError('Protocol drift')
    out = HERE / 'native_history'
    out.mkdir(exist_ok=False)
    paths = [ROOT / 'freqtrade/data/equity_provider.py',
             ROOT / 'freqtrade/data/history/equity.py', HERE / 'RecencySignal.py',
             HERE / 'bootstrap.py', Path(__file__)]
    save(HERE / 'implementation_freeze.json', {str(p): sha(p) for p in paths})
    schedules = {}
    for market, rec in frozen['schedules'].items():
        if sha(rec['path']) != rec['sha256']:
            raise ValueError('Calendar drift')
        frame = pd.read_csv(rec['path'], index_col=0)
        frame.index = pd.to_datetime(frame.index, utc=True)
        schedules[market] = frame
    declared = {}
    originals = {}
    for rec in frozen['records']:
        if sha(rec['staged']) != rec['staged_sha256'] or sha(rec['source']) != rec['source_sha256']:
            raise ValueError('Price source drift')
        frame = pd.read_parquet(rec['staged'])
        originals[rec['pair']] = frame
        FeatherDataHandler(out).ohlcv_store(rec['pair'], '1d', frame, CandleType.SPOT)
        contract = EquitySessionContract(rec['pair'], rec['calendar'],
                    tuple(schedules[rec['calendar']].index.strftime('%Y-%m-%d')))
        declared[rec['pair']] = EquitySchedule(contract, schedules[rec['calendar']])
    config = dict(runmode=RunMode.BACKTEST, timeframe='1d', datadir=out,
                  candle_type_def=CandleType.SPOT, equity_diagnostic_only=True,
                  strategy='RecencySignal', strategy_path=str(HERE),
                  user_data_dir=ROOT / 'user_data', trading_mode='spot',
                  stake_currency='USD', stake_amount=100.)
    provider = EquityDataProvider(config, declared)
    strategy = StrategyResolver.load_strategy(config)
    strategy.dp = provider
    strategy.bot_start()
    features = []
    rows = []
    scalar_cells = 0
    prefix_cells = 0
    callback_checks = 0
    for n, rec in enumerate(frozen['records']):
        pair = rec['pair']
        try:
            frame = provider.historic_ohlcv(pair, '1d')
        except EquityAdmissionError as exc:
            rows.append(dict(symbol=rec['symbol'], pair=pair, market=rec['calendar'],
                             status='rejected', reason=str(exc)))
            continue
        pd.testing.assert_frame_equal(frame, originals[pair], check_exact=True, check_dtype=False)
        advised = strategy.advise_indicators(frame.copy(), {'pair': pair})
        # Independent scalar formula uses actual dated windows and exact latest ties.
        dates = pd.DatetimeIndex(frame.date)
        values = frame.close.to_numpy()
        expected = np.full(len(frame), np.nan)
        for i, day in enumerate(dates):
            if day < dates[0] + pd.Timedelta(days=364):
                continue
            first = dates.searchsorted(day - pd.Timedelta(days=364), side='right')
            window = values[first:i + 1].tolist()
            high = max(window)
            last = max(j for j, price in enumerate(window, first) if price == high)
            expected[i] = 1 - (day - dates[last]).days / 364
            scalar_cells += 1
        np.testing.assert_array_equal(advised.calendar_high_recency, expected)
        for cutoff in ['2025-03-03', '2025-12-01', '2026-06-01']:
            before = frame.date < pd.Timestamp(cutoff, tz='UTC')
            modified = frame.copy()
            modified.loc[~before, 'close'] *= 1e5
            perturbed = strategy.advise_indicators(modified, {'pair': pair})
            truncated = strategy.advise_indicators(frame.loc[before].copy(), {'pair': pair})
            np.testing.assert_array_equal(perturbed.calendar_high_recency.loc[before],
                                          advised.calendar_high_recency.loc[before])
            np.testing.assert_array_equal(truncated.calendar_high_recency,
                                          advised.calendar_high_recency.loc[before])
            prefix_cells += int(before.sum()) * 2
        provider._set_cached_df(pair, '1d', advised, CandleType.SPOT)
        schedule = declared[pair].frame
        # Every stock, including the exact early-close boundary; no midday bar.
        for i in [0, len(frame) // 2, len(frame) - 1]:
            for clock, count in [(schedule.open.iloc[i], i),
                                 (schedule.close.iloc[i] - pd.Timedelta(nanoseconds=1), i),
                                 (schedule.close.iloc[i], i + 1)]:
                provider._set_dataframe_max_date(clock)
                assert len(provider.get_pair_dataframe(pair)) == count
                cached, _ = provider.get_analyzed_dataframe(pair, '1d')
                assert len(cached) == min(count, 1000)
                callback_checks += 2
        selected = advised[['date', 'open', 'close', 'calendar_high_recency']].copy()
        selected['pair'] = pair
        selected['market'] = rec['calendar']
        features.append(selected)
        rows.append(dict(symbol=rec['symbol'], pair=pair, market=rec['calendar'],
                         status='admitted', native_rows=len(frame),
                         exact_price_volume_cells=len(frame) * 5,
                         finite_feature_cells=int(np.isfinite(expected).sum())))
        if n % 50 == 0:
            print('NATIVE', n, 'of', len(frozen['records']), flush=True)
    pd.concat(features, ignore_index=True).to_parquet(HERE / 'native_features.parquet', index=False)
    try:
        strategy.bot_loop_start()
    except EquityAdmissionError as exc:
        guard = str(exc)
    else:
        raise AssertionError('Financial loop unexpectedly admitted')
    result = dict(owner=owner, native_strategy_type=str(type(strategy)),
                  provider_is_local_extension=True, native_strategy_resolver=True,
                  records=rows, admitted=sum(r['status'] == 'admitted' for r in rows),
                  rejected=sum(r['status'] == 'rejected' for r in rows),
                  independent_scalar_cells=scalar_cells, actual_prefix_cells=prefix_cells,
                  actual_callback_boundary_checks=callback_checks,
                  funded_loop_guard=guard, native_funded_accounts=0,
                  feature_path=str(HERE / 'native_features.parquet'),
                  feature_sha256=sha(HERE / 'native_features.parquet'),
                  source_corporate_action_units_certified=False,
                  research_path_passed=True, full_universe_admitted=False)
    save(HERE / 'native_admission.json', result)
    print('NATIVE COMPLETE', result['admitted'], 'admitted', result['rejected'], 'rejected;',
          scalar_cells, 'scalar cells;', callback_checks, 'callback checks; 0 accounts', flush=True)


if __name__ == '__main__':
    main()
