"""Boundary checks for actual fills, basis, and common-window account returns."""
# ruff: noqa: S101 -- pytest assertions are the test contract

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from research.crypto.paper_followup_20261109.review import actual_orders, date, summarize


def fixture():
    raw = {
        "id": 1,
        "is_open": True,
        "is_short": True,
        "leverage": 1,
        "amount": 10.0,
        "open_rate": 100.0,
        "open_date": "2026-11-08 21:56:49",
        "close_date": None,
    }
    order = {
        "ft_trade_id": 1,
        "ft_order_side": "sell",
        "filled": 10.0,
        "average": 100.0,
        "price": 100.0,
        "order_filled_date": "2026-11-08 21:58:50",
    }
    return raw, order


def test_actual_fill_clock_is_later_than_submission():
    raw, order = fixture()
    entry, closing = actual_orders(raw, [order])
    assert date(entry["order_filled_date"]) > date(raw["open_date"])
    assert closing is None


def test_additions_and_partial_closes_cannot_be_reported_as_zero_additions():
    raw, order = fixture()
    with pytest.raises(ValueError, match="adjustments"):
        actual_orders(raw, [order, deepcopy(order)])
    closing = {**order, "ft_order_side": "buy", "filled": 5.0}
    raw.update(is_open=False, close_rate=100.0, close_date=order["order_filled_date"])
    with pytest.raises(ValueError, match="flatten"):
        actual_orders(raw, [order, closing])


def test_native_basis_disagreement_is_not_silently_repaired():
    raw, order = fixture()
    order["average"] = 100.01
    with pytest.raises(ValueError, match="basis"):
        actual_orders(raw, [order])


def test_returns_use_common_window_nav_and_drawdown_keeps_the_recovered_peak():
    start = datetime(2026, 11, 7, tzinfo=UTC)
    grid = [start + timedelta(minutes=i) for i in range(4)]
    values = np.array([1100.0, 1210.0, 1089.0, 1197.9])
    result = summarize(values, grid, start, [], [])
    assert result["net_return_pct"] == pytest.approx(8.9)
    assert result["minute_drawdown_pct"] == pytest.approx(10.0)
