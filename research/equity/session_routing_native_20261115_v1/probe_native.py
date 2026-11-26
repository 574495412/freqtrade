"""Export actual native generator + fill/cash boundary evidence on fixtures."""
import json
from dataclasses import asdict
from unittest.mock import patch

from test_sessions import routed, fill, zero_trade
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.optimize.equity_nav import capture_equity_nav
from freqtrade.persistence import LocalTrade, Trade
from prepare import HERE


def main():
    old = Trade.use_db
    scenarios = []
    Trade.use_db = False
    try:
        with patch.object(Trade, 'commit'):
            for market in ['XNYS', 'XHKG']:
                LocalTrade.reset_trades()
                a, pair, source = routed(market)
                p = a.equity_session_plan
                data = a._get_ohlcv_as_lists({pair: source})
                callbacks, snapshots = [], []

                def callback(current_time, **kwargs):
                    visible, known = a.dataprovider.get_analyzed_dataframe(pair, '1d')
                    assert known < current_time and a.wallets.asof == current_time
                    callbacks.append(dict(at=current_time.isoformat(), known_close=known.isoformat(), signal_tag=visible.iloc[-1].enter_tag))

                a.strategy.bot_loop_start = callback
                snapshots.append(asdict(capture_equity_nav(a.wallets, [], at=a.wallets.asof, mark_kind='open')))
                generator = a.time_pair_generator(p.opens[0], p.opens[-1], [pair], data)
                first = next(generator)
                t = zero_trade(pair, first[0])
                fill(a, t, first[0], 2, 100)
                fill(a, t, first[0], 2, 100, False)
                snapshots.append(asdict(capture_equity_nav(a.wallets, [], at=a.wallets.asof, mark_kind='open')))
                for _ in generator:
                    snapshots.append(asdict(capture_equity_nav(a.wallets, [], at=a.wallets.asof, mark_kind='open')))
                assert snapshots[0]['net_nav'] == 1000
                assert all(abs(s['net_nav'] - 997.6) < 1e-8 for s in snapshots[1:])
                assert snapshots[1]['unsettled_receivable'] > 0
                assert snapshots[-1]['unsettled_receivable'] == 0
                scenarios.append(dict(market=market, callbacks=callbacks, snapshots=snapshots,
                    orders=[dict(trade_id=t.id, side=o.ft_order_side, quantity=o.safe_filled, price=o.safe_price, at=o.order_filled_utc.isoformat()) for o in t.orders],
                    native_generator=Backtesting.time_pair_generator.__module__, synthetic=True))
    finally:
        LocalTrade.reset_trades()
        Trade.use_db = old
    out = dict(passed=True, scenarios=scenarios, native_synthetic_fills=4,
               generator_yields=sum(len(s['callbacks']) for s in scenarios),
               snapshots=sum(len(s['snapshots']) for s in scenarios),
               historical_strategy_accounts=0, performance=None,
               note='ABC fixtures, explicit synthetic fees/calendars; direct native generator and fill calls, not Configuration/Backtesting.start financial runs')
    (HERE / 'native_clock_trace.json').write_text(json.dumps(out, indent=2, default=str) + '\n')
    print('PASSED native synthetic clock/fill/settlement trace; 0 historical accounts')


if __name__ == '__main__':
    main()
