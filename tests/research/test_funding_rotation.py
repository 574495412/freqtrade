"""Causal weekly funding selection, complete-universe ranking and restart admission rules."""

from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from freqtrade.persistence import Trade
from research.crypto.strategies.funding_rotation import (
    FundingRotation,
    select_week,
    weekly_exit_time,
)


def test_weekly_selection_is_disjoint_and_independent_of_symbol_order():
    values = {"A": 0.01, "B": 0.0, "C": 0.0, "D": -0.01, "E": 0.0}
    first = select_week(pd.Timestamp("2024-01-01T01:00Z"), values, list(values))
    second = select_week(pd.Timestamp("2024-01-01T01:00Z"), values, list(reversed(values)))
    assert first == second
    assert sum(row["rotation_long"] for row in first) == 2
    assert sum(row["rotation_short"] for row in first) == 2
    assert not any(row["rotation_long"] and row["rotation_short"] for row in first)
    assert next(row for row in first if row["symbol"] == "A")["rotation_long"]
    assert next(row for row in first if row["symbol"] == "D")["rotation_short"]


def test_unknown_or_equal_cross_section_does_not_create_funding_edge():
    for values in (
        {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0},
        {"A": np.nan, "B": 0.01, "C": -0.01, "D": 0.0},
    ):
        rows = select_week(pd.Timestamp("2024-01-01T01:00Z"), values, list(values))
        assert not any(row["rotation_long"] or row["rotation_short"] for row in rows)
        assert not any(row["eligible_week"] for row in rows)
    with pytest.raises(ValueError, match="complete registered universe"):
        select_week(pd.Timestamp("2024-01-01T01:00Z"), {"A": 1.0}, ["A", "B", "C", "D"])


def test_exit_deadline_survives_year_boundary_and_late_restart():
    opened = datetime(2024, 12, 30, 2, tzinfo=UTC)
    deadline = datetime(2025, 1, 6, 1, tzinfo=UTC)
    assert weekly_exit_time(opened) == deadline
    trade = SimpleNamespace(open_date_utc=opened)
    strategy = FundingRotation({})
    assert strategy.custom_exit("PAIR", trade, deadline, 1.0, -0.01) == "weekly_rebalance"
    assert strategy.custom_exit("PAIR", trade, datetime(2025, 1, 7, tzinfo=UTC), 1.0, 0.0)


def test_weekly_entry_rejects_expiry_and_duplicate_filled_trade(monkeypatch):
    strategy = FundingRotation({})
    kwargs = {
        "pair": "A/USDT:USDT",
        "order_type": "limit",
        "amount": 1,
        "rate": 1,
        "time_in_force": "GTC",
        "entry_tag": "weekly_funding_dispersion",
        "side": "long",
    }
    monkeypatch.setattr(Trade, "get_trades_proxy", lambda **kw: [])
    assert strategy.confirm_trade_entry(
        current_time=datetime(2024, 1, 1, 2, 4, tzinfo=UTC), **kwargs
    )
    assert not strategy.confirm_trade_entry(
        current_time=datetime(2024, 1, 1, 2, 5, tzinfo=UTC), **kwargs
    )
    monkeypatch.setattr(
        Trade, "get_trades_proxy", lambda **kw: [SimpleNamespace(nr_of_successful_entries=1)]
    )
    assert not strategy.confirm_trade_entry(
        current_time=datetime(2024, 1, 1, 2, 1, tzinfo=UTC), **kwargs
    )


def test_future_weekly_panel_cannot_change_past_entries(monkeypatch):
    strategy = FundingRotation({})
    clocks = pd.to_datetime(["2024-01-01T01:00Z", "2024-01-08T01:00Z"])
    panel = pd.DataFrame(
        {
            "date": clocks,
            "symbol": "AUSDT",
            "funding_carry_24h": [0.01, 0.02],
            "rotation_long": [True, True],
            "rotation_short": False,
        }
    )
    base = pd.DataFrame(
        {"date": pd.date_range("2024-01-01", periods=4, freq="h", tz="UTC"), "volume": 1.0}
    )
    monkeypatch.setattr(strategy, "panel", lambda: panel)
    first = strategy.populate_entry_trend(
        strategy.populate_indicators(base.copy(), {"pair": "A/USDT:USDT"}), {}
    )
    panel.loc[1, ["rotation_long", "rotation_short", "funding_carry_24h"]] = [False, True, -999]
    second = strategy.populate_entry_trend(
        strategy.populate_indicators(base.copy(), {"pair": "A/USDT:USDT"}), {}
    )
    pd.testing.assert_frame_equal(first, second)
    assert first.enter_long.tolist() == [0, 1, 0, 0]
