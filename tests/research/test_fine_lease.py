from types import SimpleNamespace

import pandas as pd
import pytest

from freqtrade.persistence import LocalTrade
from research.crypto.replay_fine_lease import replay
from research.crypto.run_fine_lease_account import GatedMinuteBacktesting
from research.crypto.run_ranked_first_hit_account import CachedRankedBacktesting
from research.crypto.strategies.fine_lease import FineLeaseOneR, FineLeaseQuarter, FineLeaseRetain


def minute_fixture(side=1):
    clock = pd.date_range("2024-08-01", periods=1500, freq="min", tz="UTC", name="date")
    prices = pd.DataFrame(
        {
            "open": 100.0,
            "high": 100.05,
            "low": 99.95,
            "close": 100.0,
            "volume": 1.0,
            "known_tick": 0.1,
        },
        index=clock,
    )
    fund = pd.DataFrame({"funding_rate": [0.0]}, index=pd.DatetimeIndex([clock[12]], name="date"))
    marks = pd.Series(100.0, index=clock, name="open")
    unit = SimpleNamespace(
        feature_open=clock[0],
        parent_date=clock[0],
        sleeve="small",
        side=side,
        atr=0.05,
        small_reward_r=1.0,
        retain_small=True,
    )
    return unit, prices, fund, marks


def set_open(prices, index, rate):
    prices.loc[prices.index[index], ["open", "high", "low", "close"]] = [
        rate,
        rate + 0.02,
        rate - 0.02,
        rate,
    ]


@pytest.mark.parametrize("side", [1, -1])
def test_profit_retention_requires_an_observed_open_not_current_high(side):
    unit, prices, fund, marks = minute_fixture(side)
    set_open(prices, 11, 100 + side * 0.6)
    set_open(prices, 12, 100 + side * 0.2)
    result = replay(unit, prices, fund, marks)
    assert result["exit_time"] == prices.index[12]
    assert result["exit_reason"] == "small_profit_retrace" and result["net_label"] > 0
    unit, prices, fund, marks = minute_fixture(side)
    prices.loc[prices.index[11], "high" if side == 1 else "low"] = 100 + side * 0.6
    set_open(prices, 12, 100 + side * 0.2)
    result = replay(unit, prices, fund, marks)
    assert result["exit_reason"] == "first_hit_time"


@pytest.mark.parametrize("side", [1, -1])
def test_empty_minute_cannot_fill_or_arm_and_accrued_funding_is_not_lost(side):
    unit, prices, fund, marks = minute_fixture(side)
    set_open(prices, 11, 100 + side * 0.6)
    set_open(prices, 12, 100 - side * 3)
    prices.loc[prices.index[12], "volume"] = 0
    fund.iloc[0, 0] = side * 0.0005
    set_open(prices, 13, 100 + side * 0.2)
    result = replay(unit, prices, fund, marks)
    assert result["exit_time"] == prices.index[13]
    assert result["exit_reason"] == "small_profit_retrace"
    assert result["funding_credit"] == pytest.approx(-0.0005)
    assert result["net_label"] < 0


def test_timeout_waits_for_next_observed_trade_and_unpriced_entry_is_rejected():
    unit, prices, fund, marks = minute_fixture()
    prices.loc[prices.index[250], "volume"] = 0
    result = replay(unit, prices, fund, marks)
    assert result["exit_time"] == prices.index[251] and result["exit_reason"] == "first_hit_time"
    prices.loc[prices.index[10], "volume"] = 0
    with pytest.raises(ValueError, match="Entry has no observed"):
        replay(unit, prices, fund, marks)


@pytest.mark.parametrize("side", [1, -1])
def test_core_profit_checkpoint_stays_five_minute_while_stop_path_is_fine(side):
    unit, prices, fund, marks = minute_fixture(side)
    unit.sleeve = "core"
    unit.parent_date = prices.index[5]
    set_open(prices, 11, 100 + side * 4)
    set_open(prices, 15, 100 + side * 4)
    result = replay(unit, prices, fund, marks)
    assert result["exit_time"] == prices.index[15]
    assert result["exit_reason"] == "first_hit_profit"


def test_native_gate_never_calls_matching_on_empty_observations(monkeypatch):
    clock = pd.Timestamp("2024-08-01T00:10Z")
    engine = object.__new__(GatedMinuteBacktesting)
    engine.detail_valid = {
        "ETH/USDT:USDT": pd.Series([False, True], index=[clock, clock + pd.Timedelta(minutes=1)])
    }
    engine.unpriced_steps = []
    monkeypatch.setattr(LocalTrade, "bt_trades_open_pp", {"ETH/USDT:USDT": []})
    seen = []
    monkeypatch.setattr(
        CachedRankedBacktesting, "backtest_loop", lambda self, *args: seen.append(args) or "native"
    )
    assert engine.backtest_loop((), "ETH/USDT:USDT", clock, "long", True) is None
    assert not seen and len(engine.unpriced_steps) == 1
    assert (
        engine.backtest_loop((), "ETH/USDT:USDT", clock + pd.Timedelta(minutes=1), "long", True)
        == "native"
    )
    assert len(seen) == 1


@pytest.mark.parametrize("strategy_type", [FineLeaseQuarter, FineLeaseOneR, FineLeaseRetain])
@pytest.mark.parametrize("side", [1, -1])
def test_actual_protected_fill_keeps_lease_identity_fixed_stop_and_reward(
    monkeypatch, strategy_type, side
):
    from freqtrade.enums import TradingMode
    from freqtrade.exchange.exchange import TICK_SIZE
    from freqtrade.persistence.custom_data import CustomDataWrapper
    from freqtrade.strategy.strategy_wrapper import strategy_safe_wrapper

    monkeypatch.setattr(CustomDataWrapper, "use_db", False)
    monkeypatch.setattr(CustomDataWrapper, "custom_data", [])
    clock = pd.Timestamp("2024-08-01T00:10Z")
    strategy = strategy_type({"fee": 0.0008, "research_initial_equity_usdt": 1000})
    strategy.closed_row = lambda *args: SimpleNamespace(
        feature_atr=0.05, lease_id="ETHUSDT:7", parent_date=clock - pd.Timedelta(minutes=10)
    )
    trade = LocalTrade(
        id=812,
        pair="ETH/USDT:USDT",
        open_date=clock.to_pydatetime(),
        open_rate=100,
        amount=0.01,
        stake_amount=0.5,
        leverage=2,
        fee_open=0.0008,
        fee_close=0.0008,
        is_short=side == -1,
        trading_mode=TradingMode.FUTURES,
        price_precision=0.1,
        precision_mode_price=TICK_SIZE,
        enter_tag="small_first_hit",
    )
    trade.adjust_stop_loss(100, strategy.stoploss, initial=True)
    strategy_safe_wrapper(strategy.order_filled)(
        pair=trade.pair,
        trade=trade,
        order=SimpleNamespace(ft_order_side=trade.entry_side),
        current_time=clock,
    )
    strategy._ft_stop_uses_after_fill = True
    strategy.ft_stoploss_adjust(100, trade, clock, trade.calc_profit_ratio(100), 0, after_fill=True)
    expected = (0.008 + 0.0008 * (2 - side * 0.008)) * 2 / (1 + side * 0.0008)
    assert trade.get_custom_data("small_one_r_roi") == pytest.approx(expected)
    assert trade.get_custom_data("target_roi") == pytest.approx(expected * strategy.small_reward_r)
    assert trade.get_custom_data("lease_id") == "ETHUSDT:7"
    assert trade.stop_loss == pytest.approx(100 - side * 0.8)
