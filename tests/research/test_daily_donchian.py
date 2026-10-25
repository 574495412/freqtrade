"""Completed-day clocks and fixed entry risk must survive newer market data."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from freqtrade.enums import CandleType
from research.crypto.strategies.daily_donchian import DailyDonchian, signal_atr


def strategy_with_frame(frame):
    strategy = DailyDonchian({"stake_currency": "USDT", "candle_type_def": CandleType.FUTURES})
    strategy.dp = SimpleNamespace(get_analyzed_dataframe=lambda *a: (frame, None))
    strategy.wallets = SimpleNamespace(get_total_stake_amount=lambda: 1000)
    return strategy


def test_atr_uses_exact_completed_hour_and_ignores_future_rows():
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01T01:00Z", periods=3, freq="h"),
            "atr_1d": [5.0, 9999.0, 99999.0],
        }
    )
    assert signal_atr(frame, datetime(2024, 1, 1, 2, tzinfo=UTC)) == 5
    assert signal_atr(frame, datetime(2024, 1, 1, 1, tzinfo=UTC)) is None


def test_daily_levels_are_delayed_until_next_utc_midnight():
    frame = pd.DataFrame({"date": pd.date_range("2024-01-01T22:00Z", periods=4, freq="h")})
    for column in ("channel_high", "channel_low", "exit_high", "exit_low", "atr"):
        frame[column + "_1d"] = [100.0, 200.0, 200.0, 200.0]
    strategy = strategy_with_frame(frame)
    result = strategy.populate_indicators(frame, {})
    assert result.atr_1d.iloc[1:].tolist() == [100.0, 200.0, 200.0]


def test_volatility_sizing_does_not_round_risk_up_to_exchange_minimum():
    frame = pd.DataFrame({"date": [pd.Timestamp("2024-01-01T01:00Z")], "atr_1d": [10.0]})
    strategy = strategy_with_frame(frame)
    args = ("PAIR", datetime(2024, 1, 1, 2, tzinfo=UTC), 100.0, 150.0)
    assert strategy.custom_stake_amount(*args, 5.0, 500.0, 1.0, "tag", "long") == 25.0
    assert strategy.custom_stake_amount(*args, 30.0, 500.0, 1.0, "tag", "long") == 0.0


@pytest.mark.parametrize("short,current", [(False, 120.0), (True, 90.0)])
def test_persisted_entry_atr_keeps_absolute_stop_when_latest_atr_changes(short, current):
    frame = pd.DataFrame({"date": [pd.Timestamp("2024-01-01T01:00Z")], "atr_1d": [5.0]})
    strategy = strategy_with_frame(frame)
    cache = {}
    trade = SimpleNamespace(
        open_date_utc=datetime(2024, 1, 1, 2, tzinfo=UTC),
        open_rate=100.0,
        is_short=short,
        leverage=1.0,
        get_custom_data=lambda key: cache.get(key),
        set_custom_data=lambda key, value: cache.update({key: value}),
    )
    assert strategy.fixed_atr("PAIR", trade) == 5
    frame.loc[0, "atr_1d"] = 1000
    distance = strategy.custom_stoploss("PAIR", trade, trade.open_date_utc, current, 0.0, False)
    stop = current * (1 + distance if short else 1 - distance)
    assert stop == pytest.approx(110.0 if short else 90.0)
    assert cache == {"entry_atr": 5.0}
