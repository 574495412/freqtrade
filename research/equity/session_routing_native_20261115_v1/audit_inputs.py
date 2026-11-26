"""Actual native history/resolver routing on old inputs; no funded strategy run."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from bootstrap import ROOT, HERE, boot
from prepare import sha, save

owner = boot()
from freqtrade.data.equity_provider import EquityDataProvider, EquitySchedule
from freqtrade.data.history.equity import EquityAdmissionError, EquitySessionContract
from freqtrade.enums import CandleType, RunMode, TradingMode
from freqtrade.optimize.backtesting import Backtesting, HEADERS
from freqtrade.optimize.equity_sessions import EquityDailySessionPlan, EquitySessionRoutingMixin
from freqtrade.persistence import Trade
from freqtrade.resolvers import StrategyResolver


class RoutingProbe(EquitySessionRoutingMixin, Backtesting):
    pass


def main():
    protocol = json.loads((HERE / 'protocol.json').read_text())
    amendment = json.loads((HERE / 'technical_amendment.json').read_text()) if (HERE / 'technical_amendment.json').exists() else {}
    for p, expected in amendment.get('current_components', protocol['local_components']).items():
        assert sha(p) == expected, p
    assert sha(protocol['parent_inputs']) == protocol['parent_inputs_sha256']
    assert sha(protocol['parent_admission']) == protocol['parent_admission_sha256']
    prior = json.loads(Path(protocol['parent_admission']).read_text())
    frozen = json.loads(Path(protocol['parent_inputs']).read_text())
    parent = Path(protocol['parent_inputs']).parent
    schedule_frames = {}
    for market, rec in frozen['schedules'].items():
        assert sha(rec['path']) == rec['sha256']
        frame = pd.read_csv(rec['path'], index_col=0)
        frame.index = pd.to_datetime(frame.index, utc=True)
        schedule_frames[market] = frame
    records, routed_rows, scalar_cells, callback_checks = [], 0, 0, 0
    Trade.use_db = False
    for number, rec in enumerate(frozen['records']):
        assert sha(rec['source']) == rec['source_sha256']
        assert sha(rec['staged']) == rec['staged_sha256']
        pair, market = rec['pair'], rec['calendar']
        sc = EquitySessionContract(pair, market, tuple(schedule_frames[market].index.strftime('%Y-%m-%d')))
        schedules = {pair: EquitySchedule(sc, schedule_frames[market])}
        plan = EquityDailySessionPlan(schedules, first_label=pd.Timestamp('2025-01-02', tz='UTC'), last_label=pd.Timestamp('2026-08-31', tz='UTC'))
        config = dict(runmode=RunMode.BACKTEST, dry_run=True, timeframe='1d', datadir=parent / 'native_history',
                      candle_type_def=CandleType.SPOT, strategy='ClockProbe', strategy_path=str(HERE),
                      user_data_dir=ROOT / 'user_data', trading_mode='spot', stake_currency=plan.currency, stake_amount=100.)
        provider = EquityDataProvider(config, schedules)
        try:
            frame = provider.historic_ohlcv(pair, '1d')
        except EquityAdmissionError as exc:
            records.append(dict(pair=pair, market=market, status='rejected', reason=str(exc)))
            continue
        strategy = StrategyResolver.load_strategy(config)
        strategy.dp = provider
        a = object.__new__(RoutingProbe)
        a.config, a.strategy, a.dataprovider, a.equity_session_plan = config, strategy, provider, plan
        a.timeframe, a.timeframe_detail_td, a.dynamic_pairlist = '1d', None, False
        a.trading_mode, a._can_short = TradingMode.SPOT, False
        a._set_progress_step = lambda *args: None
        a._increment_progress = a.check_abort = lambda: None
        data = a._get_ohlcv_as_lists({pair: strategy.advise_indicators(frame.copy(), {'pair': pair})})[pair]
        arr = pd.DataFrame(data, columns=HEADERS)
        target = frame.set_index('date').loc[list(plan.labels)]
        np.testing.assert_array_equal(arr[['open', 'high', 'low', 'close']].to_numpy(), target[['open', 'high', 'low', 'close']].to_numpy())
        raw = frame.set_index('date')
        idx = raw.index.get_indexer(plan.labels) - 1
        previous = raw.iloc[idx]
        np.testing.assert_array_equal(arr.enter_long, (previous.close > previous.open).astype(int))
        assert arr.enter_tag.tolist() == previous.index.strftime('%Y-%m-%d').tolist()
        assert arr.date.tolist() == list(plan.opens)
        assert all(schedules[pair].frame.loc[label, 'close'] < opened for label, opened in zip(previous.index, plan.opens))
        # Complete actual mapping, selected callbacks including US DST/half-day or HK holiday gaps.
        checks = {0, len(plan.opens) - 1}
        for date in ['2025-03-10', '2025-11-03', '2025-11-28', '2026-02-20']:
            label = pd.Timestamp(date, tz='UTC')
            if label in plan.labels: checks.add(plan.labels.index(label))
        for i in sorted(checks):
            provider._set_dataframe_max_date(plan.opens[i])
            visible, known = provider.get_analyzed_dataframe(pair, '1d')
            assert known < plan.opens[i]
            assert visible.iloc[-1].date == previous.index[i]
            assert visible.iloc[-1].enter_tag == arr.enter_tag.iloc[i]
            callback_checks += 1
        routed_rows += len(arr)
        scalar_cells += len(arr) * 8  # 4 OHLC + entry flag/tag + open/known-close mapping.
        records.append(dict(pair=pair, market=market, status='admitted', routing_rows=len(arr), first_open=plan.opens[0].isoformat(), last_open=plan.opens[-1].isoformat()))
        if number % 100 == 0: print('ROUTED', number, 'of', len(frozen['records']), flush=True)
    assert [r['status'] for r in records] == [r['status'] for r in prior['records']]
    assert not Trade.get_trades_proxy()
    save('input_routing_audit.json', dict(passed=True, native_commit=owner['commit'], native_source_files_verified=len(owner['files']),
        records=records, original_inputs=len(records), admitted=sum(r['status'] == 'admitted' for r in records),
        rejected=sum(r['status'] == 'rejected' for r in records), routing_rows=routed_rows,
        independent_scalar_cells=scalar_cells, callback_checks=callback_checks,
        historical_strategy_accounts=0, factor_analysis_calls=0, new_independent_factors=0,
        native_pair_generator_real_input_runs=0, real_input_scope='native StrategyResolver/IStrategy/DataProvider/local row conversion; generator separately synthetic-tested',
        financial_performance=None))
    print('PASSED', routed_rows, 'execution rows;', callback_checks, 'callbacks; 0 funded accounts', flush=True)


if __name__ == '__main__':
    main()
