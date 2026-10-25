"""Causal signals, unequal stake budgets and genuine whole-position accounting."""

from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from freqtrade.enums import CandleType
from freqtrade.persistence import Trade
from research.crypto.autonomous_study import joint_assessment, sleeve_summary
from research.crypto.strategies.autonomous_dual_sleeve import (
    AutonomousDualSleeve,
    indicators,
    signals,
)


def strategy():
    result = AutonomousDualSleeve(
        {"stake_currency": "USDT", "candle_type_def": CandleType.FUTURES, "fee": 0.0008}
    )
    result.wallets = SimpleNamespace(get_total_stake_amount=lambda: 1000.0)
    result.closed_row = lambda *args: pd.Series({"atr": 0.5})
    return result


def test_future_prices_and_volume_cannot_change_any_past_indicator_or_signal():
    rng = np.random.default_rng(82)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, 700)))
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=700, freq="5min", tz="UTC"),
            "open": close * 0.999,
            "high": close * 1.002,
            "low": close * 0.998,
            "close": close,
            "volume": rng.uniform(10, 100, 700),
        }
    )
    full, prefix = signals(indicators(frame)), signals(indicators(frame.iloc[:500]))
    pd.testing.assert_frame_equal(full.iloc[:500], prefix)
    assert not (full.enter_long & full.enter_short).any()


def test_core_tag_takes_priority_and_ablation_keeps_small_signal():
    frame = pd.DataFrame(
        {
            "open": [100, 100],
            "close": [99, 101],
            "atr": [0.5, 0.5],
            "volume": [1, 1],
            "volume_ratio": [1.7, 1.7],
            "mean_48": [100, 100],
            "mean_144": [98, 98],
            "mean_288": [97, 97],
            "pullback": [-0.01, -0.01],
            "rsi": [30, 40],
            "slope": [0.01, 0.01],
            "channel_high": [100, 100],
            "channel_low": [90, 90],
        }
    )
    assert signals(frame).iloc[-1].enter_tag == "core_breakout"
    assert signals(frame, allow_core=False).iloc[-1].enter_tag == "small_pullback"


@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize(
    "tag,risk,margin", [("small_pullback", 0.0005, 0.02), ("core_breakout", 0.005, 0.20)]
)
def test_initial_stop_loss_and_fees_respect_sleeve_budget(side, tag, risk, margin):
    result = strategy()
    distance = result.distance(result.closed_row(None, None), 100.0, tag)
    stake = result.custom_stake_amount(
        "P/USDT:USDT", datetime(2024, 1, 1, tzinfo=UTC), 100, 999, 5, 1000, 2, tag, side
    )
    sign = 1 if side == "long" else -1
    loss = stake * 2 * (distance + 0.0008 + 0.0008 * (1 - sign * distance))
    assert stake <= 1000 * margin
    assert loss <= 1000 * risk + 1e-10
    assert stake > 0


def test_minimum_lot_does_not_force_small_stake_above_its_budget():
    result = strategy()
    assert (
        result.custom_stake_amount(
            "P/USDT:USDT",
            datetime(2024, 1, 1, tzinfo=UTC),
            100,
            999,
            21,
            1000,
            2,
            "small_pullback",
            "long",
        )
        == 0
    )


def test_unavailable_registered_leverage_rejects_entry():
    result = strategy()
    assert (
        result.custom_stake_amount(
            "P/USDT:USDT",
            datetime(2024, 1, 1, tzinfo=UTC),
            100,
            999,
            5,
            1000,
            1,
            "core_breakout",
            "long",
        )
        == 0
    )


@pytest.mark.parametrize("tag,maximum", [("small_pullback", 2), ("core_breakout", 4)])
def test_sleeve_slot_caps_use_open_trades_from_the_same_account(monkeypatch, tag, maximum):
    result = strategy()
    monkeypatch.setattr(
        Trade, "get_trades_proxy", lambda **kwargs: [SimpleNamespace(enter_tag=tag)] * maximum
    )
    assert not result.confirm_trade_entry(
        "P/USDT:USDT", "limit", 1, 100, "gtc", datetime(2024, 1, 1, tzinfo=UTC), tag, "long"
    )


def test_runtime_lookup_uses_exact_completed_candle_even_with_future_rows():
    result = strategy()
    del result.closed_row
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=4, freq="5min", tz="UTC"),
            "close": [100, 101, 9999, 9999],
        }
    )
    result.dp = SimpleNamespace(get_analyzed_dataframe=lambda *args: (frame, None))
    assert result.closed_row("P/USDT:USDT", datetime(2024, 1, 1, 0, 10, tzinfo=UTC)).close == 101


def test_missing_filled_risk_state_forces_full_exit():
    result = strategy()
    trade = SimpleNamespace(get_custom_data=lambda key: None)
    assert result.custom_exit("P/USDT:USDT", trade, None, 100, 1.0) == "risk_state_unavailable"


def test_many_small_wins_cannot_hide_one_large_loss():
    def trade(profit, notional):
        return {
            "profit_abs": profit,
            "amount": 1,
            "open_rate": notional,
            "is_short": False,
            "trade_duration": 5,
        }

    metrics = sleeve_summary([trade(0.1, 10)] * 99 + [trade(-50, 1000)])
    assert metrics["win_rate"] == 0.99
    assert metrics["net_profit_usdt"] < 0
    assert metrics["entry_notional_weighted_win_rate"] < 0.5


def test_joint_objective_rejects_high_win_rate_with_low_account_return():
    result = {
        "start": "2024-01-01",
        "end": "2025-01-01",
        "win_rate": 0.95,
        "net_return": 0.01,
        "economic_audit": {"reasons": []},
    }
    sleeves = {
        "small_pullback": {"closed_positions": 2000, "net_profit_usdt": 5},
        "core_breakout": {"closed_positions": 50, "net_profit_usdt": 5},
    }
    plan = {
        "joint_targets": {
            "annual_compounded_net_return_target": 0.5,
            "minimum_net_whole_position_win_rate": 0.8,
            "minimum_small_complete_trades_per_calendar_day": 3,
        }
    }
    assessment = joint_assessment(result, sleeves, plan)
    assert not assessment["passed"]
    assert assessment["period_return_hurdle"] > 0.5
