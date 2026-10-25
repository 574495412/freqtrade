"""No future ranks, invented flow, unrealized profit authority or repeated additions."""

from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from freqtrade.enums import CandleType
from research.crypto.prepare_perpetual_flow import public_features
from research.crypto.strategies.perpetual_flow import PerpFlowFunded


def market():
    rng = np.random.default_rng(29)
    index = pd.date_range("2024-01-01", periods=200, freq="5min", tz="UTC")
    columns = [f"S{i:02}" for i in range(40)]
    close = pd.DataFrame(
        100 * np.exp(np.cumsum(rng.normal(0, 0.01, (200, 40)), axis=0)),
        index=index,
        columns=columns,
    )
    quotes = pd.DataFrame(np.exp(rng.normal(10, 1, (200, 40))), index=index, columns=columns)
    taker = quotes * pd.DataFrame(rng.uniform(0.1, 0.9, (200, 40)), index=index, columns=columns)
    return close, quotes, taker


def test_future_market_rows_cannot_change_past_cross_section_or_edges():
    inputs = market()
    full = public_features(*inputs)
    clipped = public_features(*(frame.iloc[:120] for frame in inputs))
    for key in full:
        pd.testing.assert_frame_equal(full[key].iloc[:120], clipped[key])
    assert full["entry"].abs().sum(axis=1).max() == 1


def test_unknown_flow_does_not_authorize_entry_or_a_false_recovery_edge():
    close, quotes, taker = market()
    quotes.iloc[100, 0] = taker.iloc[100, 0] = 0
    features = public_features(close, quotes, taker)
    assert features["entry"].iloc[100:102].abs().sum().sum() == 0
    assert np.isnan(features["flow_5m"].iloc[100, 0])


def funded_state():
    strategy = PerpFlowFunded({"stake_currency": "USDT", "candle_type_def": CandleType.FUTURES})
    row = pd.Series(
        {
            "date": pd.Timestamp("2024-01-01T00:10Z"),
            "direction": 1,
            "residual_20_pct": 1.0,
            "flow_5m": 0.2,
            "risk_atr": 1.0,
        }
    )
    strategy.closed_row = lambda *args: row
    cache = {
        "fixed_stop_price": 98.0,
        "initial_margin": 150.0,
        "harvest_fill_time": "2024-01-01T00:05:00+00:00",
    }
    trade = SimpleNamespace(
        pair="P/USDT:USDT",
        open_rate=100.0,
        is_short=False,
        leverage=1.0,
        stake_amount=75.0,
        realized_profit=1.0,
        nr_of_successful_entries=1,
        nr_of_successful_exits=1,
        has_open_orders=False,
        get_custom_data=lambda key: cache.get(key),
    )
    return strategy, trade, row, cache


def test_addition_stop_risk_is_bounded_by_filled_net_profit():
    strategy, trade, _, _ = funded_state()
    stake, tag = strategy.adjust_trade_position(
        trade, datetime(2024, 1, 1, 0, 15, tzinfo=UTC), 100.0, 0.5, 5.0, 200.0
    )
    assert stake == pytest.approx(50.0)
    assert stake * 0.02 <= trade.realized_profit
    assert tag == "realized_profit_authorized_add"


@pytest.mark.parametrize(
    "case",
    [
        "negative_realized",
        "pending",
        "already_added",
        "minimum_lot",
        "same_harvest_bar",
        "lost_direction",
    ],
)
def test_unavailable_or_consumed_profit_cannot_add(case):
    strategy, trade, row, cache = funded_state()
    minimum = 5.0
    if case == "negative_realized":
        trade.realized_profit = -0.1
    elif case == "pending":
        trade.has_open_orders = True
    elif case == "already_added":
        trade.nr_of_successful_entries = 2
    elif case == "minimum_lot":
        minimum = 51.0
    elif case == "same_harvest_bar":
        cache["harvest_fill_time"] = "2024-01-01T00:10:01+00:00"
    else:
        row["direction"] = -1
    assert (
        strategy.adjust_trade_position(
            trade, datetime(2024, 1, 1, 0, 15, tzinfo=UTC), 100.0, 1.0, minimum, 200.0
        )
        is None
    )


def test_price_threshold_is_not_unrealized_net_profit_authority():
    strategy, trade, _, _ = funded_state()
    trade.nr_of_successful_exits = 0
    trade.stake_amount = 150.0
    clock = datetime(2024, 1, 1, 0, 15, tzinfo=UTC)
    assert strategy.adjust_trade_position(trade, clock, 100.5, 99.0, 5.0, 200.0) is None
    assert strategy.adjust_trade_position(trade, clock, 101.2, -0.1, 5.0, 200.0) == (
        -75.0,
        "half_profit_harvest",
    )
