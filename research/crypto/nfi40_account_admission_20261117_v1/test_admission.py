"""Consequential exposure and information-clock boundaries; no new finance."""

from decimal import Decimal

import numpy as np
import pandas as pd
from account_audit import SLOT, exposure
from precision_audit import get_tick_size_over_time, strict_past_count


def trade(opened, closed, short=False, sold=1.0):
    return {
        "pair": "AXS/USDT:USDT",
        "open_timestamp": opened,
        "close_timestamp": closed,
        "is_short": short,
        "orders": [
            {"order_filled_timestamp": opened, "amount": 1.0, "ft_is_entry": True},
            {"order_filled_timestamp": closed, "amount": sold, "ft_is_entry": False},
        ],
    }


def test_opposite_campaigns_never_net_away_gross_funding_exposure():
    result = exposure([trade(SLOT - 1, SLOT + 1), trade(SLOT - 1, SLOT + 1, True)], "AXS/USDT:USDT")
    assert len(result["active_native_campaigns"]) == 2
    assert Decimal(result["gross_active_serialized_quantity"]) == 2
    assert not result["no_modeled_exposure_in_entire_bracket"]


def test_boundary_fill_is_not_certified_flat_by_ordering_assumptions():
    result = exposure([trade(SLOT, SLOT + 100)], "AXS/USDT:USDT")
    assert result["boundary_fills"]
    assert not result["no_modeled_exposure_in_entire_bracket"]


def test_native_closed_serialization_residual_is_retained_and_scope_explicit():
    result = exposure(
        [trade(SLOT - 40_000_000, SLOT - 30_000_000, sold=0.9999999999)], "AXS/USDT:USDT"
    )
    assert result["no_modeled_exposure_in_entire_bracket"]
    assert result["closed_campaign_serialization_residuals"]
    assert (
        Decimal(
            result["closed_campaign_serialization_residuals"][0]["serialized_quantity_before_slot"]
        )
        != 0
    )
    assert result["source_rate_unknown"]


def test_completed_bar_clock_ignores_current_and_future_counts():
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=3, freq="5min", tz="UTC"),
            "max_count": [1.0, 2.0, 5.0],
        }
    )
    assert np.isnan(strict_past_count(frame).iloc[0])
    assert strict_past_count(frame).iloc[1:].tolist() == [1.0, 2.0]
    frame.loc[1:, "max_count"] = 12.0
    assert strict_past_count(frame).iloc[1] == 1.0


def test_monthly_count_resets_but_unknown_new_month_uses_past_native_asof():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2025-01-31T23:55Z", "2025-02-01T00:00Z", "2025-02-01T00:05Z"], utc=True
            ),
            "max_count": [3.0, 1.0, 2.0],
        }
    )
    assert strict_past_count(frame).iloc[1:].tolist() == [3.0, 1.0]
    frame.loc[1:, "max_count"] = np.nan
    assert strict_past_count(frame).iloc[1:].tolist() == [3.0, 3.0]


def test_native_full_month_heuristic_can_change_the_past_when_future_price_changes():
    dates = pd.to_datetime(
        ["2024-12-31T23:55Z", "2025-01-01T00:00Z", "2025-01-01T00:05Z", "2025-01-31T23:55Z"],
        utc=True,
    )

    def frame(future):
        return pd.DataFrame(
            {
                "date": dates,
                **{k: [10.1, 10.1, 10.1, future] for k in ["open", "high", "low", "close"]},
            }
        )

    a, b = frame(10.0001), frame(10.01)
    first, second = get_tick_size_over_time(a), get_tick_size_over_time(b)
    at = pd.Timestamp("2025-01-01", tz="UTC")
    assert first.asof(at) != second.asof(at)
    pd.testing.assert_series_equal(strict_past_count(a).iloc[:3], strict_past_count(b).iloc[:3])
