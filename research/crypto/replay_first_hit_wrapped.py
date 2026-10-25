"""Replay a path through native protected callbacks, real-trade stop adjustment and exit methods."""

from decimal import Decimal
from types import SimpleNamespace

import pandas as pd

from freqtrade.enums import TradingMode
from freqtrade.exchange import price_to_precision
from freqtrade.exchange.exchange import TICK_SIZE, Exchange
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.persistence import LocalTrade
from freqtrade.strategy.strategy_wrapper import strategy_safe_wrapper
from research.crypto.first_hit_paths import SPECS
from research.crypto.strategies.first_hit_dual_v2 import FirstHitDualFixedBase


def replay(unit, prices, funding, marks, cost_bps=3):
    begin = pd.Timestamp(unit.feature_open) + pd.Timedelta(minutes=10)
    first, fee = float(prices.loc[begin, "open"]), 0.0005 + cost_bps / 10000
    tick = float(prices.loc[begin, "known_tick"])
    strategy = FirstHitDualFixedBase({"fee": fee, "research_initial_equity_usdt": 1000})
    strategy.minimal_roi = {0: 1000.0}
    strategy.dp = SimpleNamespace(runmode=SimpleNamespace(value="backtest"))
    strategy.closed_row = lambda *args: SimpleNamespace(feature_atr=unit.atr)
    state = {}
    trade = LocalTrade(
        pair="TEST/USDT:USDT",
        open_date=begin.to_pydatetime(),
        open_rate=first,
        amount=1 / first,
        stake_amount=0.5,
        leverage=2.0,
        fee_open=fee,
        fee_close=fee,
        is_short=unit.side == -1,
        trading_mode=TradingMode.FUTURES,
        orders=[],
        price_precision=tick,
        precision_mode_price=TICK_SIZE,
        enter_tag=f"{unit.sleeve}_first_hit",
    )
    trade.get_custom_data = lambda key, **kwargs: state.get(key)
    trade.set_custom_data = lambda key, value: state.update({key: value})
    trade.adjust_stop_loss(first, strategy.stoploss, initial=True)
    strategy_safe_wrapper(strategy.order_filled)(
        pair=trade.pair,
        trade=trade,
        order=SimpleNamespace(ft_order_side=trade.entry_side),
        current_time=begin,
    )
    strategy._ft_stop_uses_after_fill = True
    strategy.ft_stoploss_adjust(
        first, trade, begin, trade.calc_profit_ratio(first), 0, after_fill=True
    )
    if not state:
        raise ValueError("Unit was not eligible for the native entry risk limits")
    locked = trade.stop_loss
    engine = object.__new__(Backtesting)
    engine.strategy, engine.timeframe_min = strategy, 5
    combined = Exchange.combine_funding_and_mark(
        funding.reset_index(), marks.rename("open").reset_index()
    )
    finish = begin + pd.Timedelta(hours=SPECS[unit.sleeve]["hours"])
    for clock, candle in prices.loc[begin:finish].iterrows():
        trade.funding_fees = Exchange.calculate_funding_fees(
            None, combined, trade.amount, trade.is_short, begin, clock
        )
        exits = strategy.should_exit(
            trade,
            float(candle.open),
            clock.to_pydatetime(),
            enter=False,
            exit_=False,
            low=float(candle.low),
            high=float(candle.high),
        )
        if trade.stop_loss != locked or trade.is_stop_loss_trailing:
            raise ValueError("Native hard stop moved or became trailing")
        if not exits:
            continue
        chosen = exits[0]
        row = (clock, candle.open, candle.high, candle.low, candle.close)
        rate = engine._get_close_rate(
            row, trade, clock, chosen, int((clock - begin).total_seconds() / 60)
        )
        rate = price_to_precision(rate, tick, TICK_SIZE)
        if not candle.low <= rate <= candle.high:
            raise ValueError("Rounded native exit price would not fill in this candle")
        events = funding.loc[(funding.index >= begin) & (funding.index <= clock)]
        cash = Decimal(0)
        q, direction, f = 1 / Decimal(str(first)), Decimal(int(unit.side)), Decimal(str(fee))
        for when, event in events.iterrows():
            mark = marks.asof(when)
            cash -= direction * q * Decimal(str(mark)) * Decimal(str(event.funding_rate))
        net = direction * q * (Decimal(str(rate)) - Decimal(str(first)))
        net += cash - q * (Decimal(str(first)) + Decimal(str(rate))) * f
        if abs(float(cash) - trade.funding_fees) > 1e-12:
            raise ValueError("Native funding and raw Decimal settlements differ")
        return {
            "exit_time": clock,
            "exit_price": rate,
            "exit_reason": chosen.exit_reason,
            "net_label": float(net),
            "locked_stop": locked,
            "funding_credit": float(cash),
            "target_roi": state["target_roi"],
        }
    raise ValueError("Native finite path did not terminate")
