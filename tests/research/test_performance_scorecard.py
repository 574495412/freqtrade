"""Published metric examples and account-risk boundary cases."""

import pandas as pd
import pytest

from research.crypto.performance_scorecard import (
    anchor_initial_wallet,
    annual_sharpe,
    marked_metrics,
)


def curve(days=31):
    start = pd.Timestamp("2024-01-01", tz="UTC")
    end = start + pd.Timedelta(days=days)
    return pd.Series(1000.0, index=pd.date_range(start, end, freq="h")), start, end


def test_binance_published_annualized_sharpe_examples():
    # Official futures FAQ examples use sample deviation and365calendar days,rf0.
    assert round(annual_sharpe([0, 0.50]), 2) == 13.51
    assert round(annual_sharpe([0, 0.50, -0.02]), 2) == 10.38
    assert round(annual_sharpe([0, 0.50, -0.02, -0.08]), 2) == 7.11


def test_cash_is_undefined_sharpe_not_an_artificial_winner():
    equity, start, end = curve()
    metrics, days = marked_metrics(equity, 1000, start, end)
    assert len(days) == 31
    assert metrics["binance_no_transfer_sharpe_proxy"] is None
    assert metrics["nav_daily_sharpe_sample_365"] is None
    assert metrics["cagr_over_hourly_marked_mdd"] is None
    assert metrics["flat_days"] == 31


def test_floating_drawdown_remains_visible_when_final_account_is_flat():
    equity, start, end = curve()
    equity.loc[start + pd.Timedelta(hours=3)] = 700
    metrics, _ = marked_metrics(equity, 1000, start, end)
    assert metrics["net_profit_usdt"] == 0
    assert metrics["hourly_marked_mdd"] == pytest.approx(0.30)
    assert metrics["longest_underwater_days_including_unrecovered"] == pytest.approx(2 / 24)


def test_rolling_platform_base_is_not_silently_rebased_to_current_equity():
    equity, start, end = curve()
    equity[:] = 2000
    equity.loc[end] = 2100
    metrics, _ = marked_metrics(equity, 2000, start, end, max_base_balance=1000)
    assert metrics["net_return"] == pytest.approx(0.05)
    assert metrics["binance_no_transfer_roi_proxy"] == pytest.approx(0.10)


def test_missing_mark_hour_cannot_be_zero_filled():
    equity, start, end = curve()
    equity = equity.drop(start + pd.Timedelta(hours=2))
    with pytest.raises(ValueError, match="every clock"):
        marked_metrics(equity, 1000, start, end)


def test_known_initial_wallet_is_an_anchor_not_a_missing_hour_imputation():
    equity, start, end = curve()
    recorded = equity.drop(start)
    restored = anchor_initial_wallet(recorded, start, 1000, 1000, [])
    metrics, _ = marked_metrics(restored, 1000, start, end)
    assert metrics["net_profit_usdt"] == 0
    with pytest.raises(ValueError, match="pre-period"):
        anchor_initial_wallet(
            recorded, start, 1000, 1000, [{"open_timestamp": start.value // 1_000_000 - 1}]
        )


def test_losing_unrecovered_account_is_not_hidden_by_no_closed_losers():
    equity, start, end = curve()
    equity.loc[start + pd.Timedelta(hours=1) :] = 900
    metrics, _ = marked_metrics(equity, 1000, start, end)
    assert metrics["underwater_at_end"]
    assert metrics["longest_underwater_days_including_unrecovered"] == 31
    assert metrics["net_profit_usdt"] == -100
