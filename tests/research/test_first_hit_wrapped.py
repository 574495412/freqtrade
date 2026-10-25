"""Regression: protected callbacks copy Trade; only the returned stop updates the actual trade."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from freqtrade.enums import TradingMode
from freqtrade.exchange.exchange import TICK_SIZE
from freqtrade.persistence import CustomDataWrapper, LocalTrade, Order
from freqtrade.strategy.strategy_wrapper import strategy_safe_wrapper
from research.crypto.strategies.first_hit_dual import FirstHitDualBase
from research.crypto.strategies.first_hit_dual_v2 import FirstHitDualFixedBase


@pytest.mark.parametrize("side", [1, -1])
@pytest.mark.parametrize("rate,tick", [(23.9, 0.001), (100.0, 0.1), (0.05116, 0.00001)])
def test_real_trade_stop_changes_only_after_native_after_fill_update(monkeypatch, side, rate, tick):
    monkeypatch.setattr(CustomDataWrapper, "use_db", False)
    monkeypatch.setattr(CustomDataWrapper, "custom_data", [])
    strategy = FirstHitDualFixedBase({"fee": 0.0008, "research_initial_equity_usdt": 1000})
    strategy.closed_row = lambda *args: SimpleNamespace(feature_atr=rate * 0.003)
    trade = LocalTrade(
        id=1,
        pair="TEST/USDT:USDT",
        open_rate=rate,
        amount=10 / rate,
        stake_amount=5,
        leverage=2,
        fee_open=0.0008,
        fee_close=0.0008,
        is_short=side == -1,
        open_date=datetime(2024, 7, 5, tzinfo=UTC),
        trading_mode=TradingMode.FUTURES,
        price_precision=tick,
        precision_mode_price=TICK_SIZE,
        enter_tag="core_first_hit",
    )
    trade.adjust_stop_loss(rate, strategy.stoploss, initial=True)
    order = Order(
        ft_order_side=trade.entry_side,
        ft_pair=trade.pair,
        ft_is_open=True,
        amount=trade.amount,
        filled=0,
        remaining=trade.amount,
        price=rate,
        ft_price=rate,
        average=rate,
        cost=10,
        order_type="limit",
        status="open",
        order_date=trade.open_date_utc,
    )
    trade.orders.append(order)
    order.close_bt_order(trade.open_date_utc, trade)
    initial = trade.stop_loss
    strategy_safe_wrapper(strategy.order_filled)(
        pair=trade.pair,
        trade=trade,
        order=SimpleNamespace(ft_order_side=trade.entry_side),
        current_time=trade.open_date_utc,
    )
    expected = trade.get_custom_data("locked_stop")
    assert trade.stop_loss == initial and expected != initial
    strategy._ft_stop_uses_after_fill = True
    strategy.ft_stoploss_adjust(
        rate, trade, trade.open_date_utc, trade.calc_profit_ratio(rate), 0, after_fill=True
    )
    assert trade.stop_loss == expected
    assert not trade.is_stop_loss_trailing
    trade.recalc_trade_from_orders()
    assert trade.stop_loss == expected and not trade.is_stop_loss_trailing
    for factor in [1.01, 0.99, 1.02]:
        strategy.ft_stoploss_adjust(
            rate * factor, trade, trade.open_date_utc, 0, 0, after_fill=False
        )
        assert trade.stop_loss == expected and not trade.is_stop_loss_trailing


def test_v1_has_no_framework_stop_callback():
    assert FirstHitDualBase.use_custom_stoploss is False
    assert FirstHitDualFixedBase.use_custom_stoploss is True
