"""The cross-contract state uses completed prices and delayed known raw ratios."""

import numpy as np
import pandas as pd
import pytest

from research.crypto.conditional_context import context_features


def inputs():
    rng = np.random.default_rng(615)
    dates = pd.date_range("2024-01-01", periods=900, freq="5min", tz="UTC")
    panels, closes, metrics = {}, {}, {}
    for symbol in ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]:
        close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.001, len(dates)))), index=dates)
        returns = np.log(close)
        closes[symbol] = close
        panels[symbol] = pd.DataFrame(
            {
                "date": dates,
                "return20": returns.diff(4).to_numpy(),
                "return60": returns.diff(12).to_numpy(),
                "flow20": rng.normal(0, 0.1, len(dates)),
                "impact_residual20": rng.normal(size=len(dates)),
                "funding_sum24h": 0.0003,
                "oi_contraction60": 0.01,
            }
        )
        metrics[symbol] = pd.DataFrame(
            {
                "raw_time": dates,
                "count_long_short_ratio": 2.0,
                "count_long_short_ratio_value_status": "KNOWN_RAW_VALUE",
            }
        )
    return panels, closes, metrics


def test_cross_contract_future_changes_do_not_change_past_state():
    panels, closes, metrics = inputs()
    original = context_features(panels, closes, metrics)
    panels["SOLUSDT"].loc[650:, "return20"] = 1e3
    closes["ETHUSDT"].iloc[650:] *= 10
    metrics["BNBUSDT"].loc[650:, "count_long_short_ratio"] = 100
    altered = context_features(panels, closes, metrics)
    for symbol in panels:
        pd.testing.assert_frame_equal(original[symbol].iloc[:650], altered[symbol].iloc[:650])


def test_market_rotation_excludes_own_return_and_self_regression_has_no_noise_alpha():
    panels, closes, metrics = inputs()
    result = context_features(panels, closes, metrics)
    for symbol in panels:
        expected = np.mean([p.return20.iloc[500] for s, p in panels.items() if s != symbol])
        assert result[symbol].market_return20.iloc[500] == pytest.approx(expected)
    assert (result["BTCUSDT"].btc_residual60.dropna() == 0).all()


def test_global_ratio_delay_and_missing_values_are_not_imputed():
    panels, closes, metrics = inputs()
    metrics["BTCUSDT"].loc[400, "count_long_short_ratio"] = 4.0
    result = context_features(panels, closes, metrics)["BTCUSDT"]
    assert result.global_account_logratio.iloc[400] == pytest.approx(np.log(2))
    assert result.global_account_logratio.iloc[401] == pytest.approx(np.log(4))
    metrics["BTCUSDT"].loc[400, "count_long_short_ratio"] = 0
    assert np.isnan(
        context_features(panels, closes, metrics)["BTCUSDT"].global_account_logratio.iloc[401]
    )


def test_cross_asset_incomplete_clock_is_rejected():
    panels, closes, metrics = inputs()
    closes["SOLUSDT"] = closes["SOLUSDT"].iloc[1:]
    with pytest.raises(ValueError, match="exact clock"):
        context_features(panels, closes, metrics)
