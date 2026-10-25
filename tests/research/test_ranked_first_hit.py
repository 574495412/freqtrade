"""Causal dollar/risk ordering, reserved capacity, fixed-clock admissions and observation parity."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from freqtrade.enums import ExitType, TradingMode
from freqtrade.exchange.exchange import TICK_SIZE
from freqtrade.persistence import CustomDataWrapper, LocalTrade, Trade
from freqtrade.strategy.strategy_wrapper import strategy_safe_wrapper
from research.crypto.run_ranked_first_hit_account import CachedRankedBacktesting
from research.crypto.strategies.joint_intrabar_ranked import (
    JointIntrabarMonthly,
    JointRankedMonthly,
)
from research.crypto.strategies.ranked_first_hit import RankedFirstHitBase, rank_pool


def frame():
    date = pd.Timestamp("2024-08-05T00:05:00Z")
    return pd.DataFrame(
        [
            {
                "date": date,
                "symbol": "AUSDT",
                "forecast_sleeve": "core",
                "forecast_side": 1,
                "known_atr_fraction": 0.02,
                "predicted_net": 0.02,
                "row_id": 1,
            },
            {
                "date": date,
                "symbol": "BUSDT",
                "forecast_sleeve": "core",
                "forecast_side": 1,
                "known_atr_fraction": 0.002,
                "predicted_net": 0.01,
                "row_id": 2,
            },
            {
                "date": date,
                "symbol": "CUSDT",
                "forecast_sleeve": "core",
                "forecast_side": 1,
                "known_atr_fraction": 0.002,
                "predicted_net": 0.009,
                "row_id": 3,
            },
            {
                "date": date + pd.Timedelta(minutes=5),
                "symbol": "FUTUREUSDT",
                "forecast_sleeve": "core",
                "forecast_side": 1,
                "known_atr_fraction": 0.002,
                "predicted_net": 1000,
                "row_id": 4,
            },
        ]
    )


def test_rank_uses_known_dollar_profit_at_fixed_risk_not_raw_return_or_future_rows():
    data = frame()
    ranked, slots = rank_pool(data, data.date.iloc[0], "core_first_hit", [], 0, 0, 0.0008, 990)
    assert slots == 2 and ranked.symbol.tolist() == ["BUSDT", "CUSDT"]
    prefix = data.loc[data.date.eq(data.date.iloc[0])]
    actual, _ = rank_pool(prefix, prefix.date.iloc[0], "core_first_hit", [], 0, 0, 0.0008, 990)
    pd.testing.assert_frame_equal(ranked, actual, check_exact=True)


def test_existing_positions_global_capacity_and_ties_are_respected():
    data = frame()
    ranked, slots = rank_pool(
        data, data.date.iloc[0], "core_first_hit", ["B/USDT:USDT"], 1, 3, 0.0008, 990
    )
    assert slots == 1 and ranked.symbol.tolist() == ["CUSDT"]
    data.loc[data.symbol.eq("CUSDT"), "predicted_net"] = 0.01
    ranked, _ = rank_pool(
        data.iloc[::-1], data.date.iloc[0], "core_first_hit", [], 0, 0, 0.0008, 990
    )
    assert ranked.symbol.tolist() == ["BUSDT", "CUSDT"]


def test_admission_choices_are_fixed_before_serial_pair_processing(monkeypatch):
    instance = object.__new__(RankedFirstHitBase)
    instance.config = {"fee": 0.0008, "research_initial_equity_usdt": 1000}
    instance.dp = SimpleNamespace(runmode=SimpleNamespace(value="backtest"))
    instance._ranking_frame = frame()
    instance._rank_allowed = {}
    instance._rank_decisions = []
    opened = []
    monkeypatch.setattr(Trade, "get_trades_proxy", lambda **kwargs: opened)
    clock = instance._ranking_frame.date.iloc[0] + pd.Timedelta(minutes=5)

    def admitted(symbol, side="long"):
        return instance.confirm_trade_entry(
            f"{symbol}/USDT:USDT", "limit", 1, 100, "GTC", clock, "core_first_hit", side
        )

    assert not admitted("A")
    assert admitted("B") and not admitted("B", "short")
    opened.append(SimpleNamespace(pair="B/USDT:USDT", enter_tag="core_first_hit"))
    assert admitted("C") and not admitted("A")
    assert len(instance._rank_decisions) == 1 and instance._rank_decisions[0]["slots"] == 2
    opened.append(SimpleNamespace(pair="C/USDT:USDT", enter_tag="core_first_hit"))
    assert not admitted("A")
    instance.dp.runmode.value = "live"
    assert not admitted("B")


def test_cached_observer_keeps_causal_lookup_and_exact_open_pair_snapshot(monkeypatch):
    engine = object.__new__(CachedRankedBacktesting)
    engine.precision_calls = []
    clock = pd.Timestamp("2024-08-05T00:10:00Z")
    engine.closed_precision = {
        "ETH/USDT:USDT": pd.Series([0.01, 0.001], index=[clock, clock + pd.Timedelta(minutes=5)])
    }
    opened = [SimpleNamespace(pair="B/USDT:USDT", enter_tag="core_first_hit")]
    monkeypatch.setattr(Trade, "get_trades_proxy", lambda **kwargs: opened)
    assert engine.get_pair_precision("ETH/USDT:USDT", clock)[0] == 0.01
    assert engine.precision_calls[0]["open_pairs"] == ["B/USDT:USDT"]


@pytest.mark.parametrize("strategy_type", [JointIntrabarMonthly, JointRankedMonthly])
@pytest.mark.parametrize("side", [1, -1])
def test_mixed_strategy_keeps_quarter_r_profit_and_hard_stop_precedence(
    monkeypatch, strategy_type, side
):
    monkeypatch.setattr(CustomDataWrapper, "use_db", False)
    monkeypatch.setattr(CustomDataWrapper, "custom_data", [])
    strategy = strategy_type({"fee": 0.0008, "research_initial_equity_usdt": 1000})
    strategy.minimal_roi = {0: 1000.0}
    strategy.closed_row = lambda *args: SimpleNamespace(feature_atr=0.05)
    start = datetime(2024, 8, 5, tzinfo=UTC)
    trade = LocalTrade(
        id=777,
        pair="TEST/USDT:USDT",
        open_date=start,
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
        current_time=start,
    )
    strategy._ft_stop_uses_after_fill = True
    strategy.ft_stoploss_adjust(100, trade, start, trade.calc_profit_ratio(100), 0, after_fill=True)
    expected = 0.25 * (0.008 + 0.0008 * (2 - side * 0.008)) * 2 / (1 + side * 0.0008)
    assert trade.get_custom_data("target_roi") == pytest.approx(expected, abs=1e-15)
    assert trade.stop_loss == pytest.approx(100 - side * 0.8)
    exits = strategy.should_exit(trade, 100, start, enter=False, exit_=False, low=97, high=103)
    assert exits[0].exit_type == ExitType.STOP_LOSS and not trade.is_stop_loss_trailing
