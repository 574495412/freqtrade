# ruff: noqa: S101 - pytest assertions are the checks
import pandas as pd
import pytest

from research.crypto.band_trend_native_20261113_v1.analyze import combine, metrics


def test_combination_uses_real_sleeve_nav_and_does_not_reset_cash():
    a = pd.Series([500.0, 600.0, 300.0])
    b = pd.Series([500.0, 400.0, 800.0])
    total = combine(a, b)
    assert total.tolist() == [1000.0, 1000.0, 1100.0]
    assert metrics(total)["net_return"] == pytest.approx(0.1)
    assert metrics(a)["maximum_hourly_mark_drawdown"] == 0.5


def test_missing_clocks_or_wrong_wallet_sizes_fail():
    with pytest.raises(ValueError):
        combine(pd.Series([500.0, 600.0], index=[0, 1]), pd.Series([500.0, 600.0], index=[0, 2]))
    with pytest.raises(ValueError):
        combine(pd.Series([1000.0, 1100.0]), pd.Series([500.0, 600.0]))
    with pytest.raises(ValueError):
        combine(pd.Series([500.0, float("nan")]), pd.Series([500.0, 600.0]))
