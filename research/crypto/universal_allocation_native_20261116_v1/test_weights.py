"""Known rational posterior, completed-data clocks and explicit missingness."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PATH = Path(__file__).absolute().parents[3] / 'freqtrade/data/universal_allocation.py'
spec = importlib.util.spec_from_file_location('local_cover_signal', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def inputs():
    dates = pd.date_range('2025-01-01', periods=4, tz='UTC')
    prices = pd.DataFrame({'BTC/USDT': [1., 2., 1., 3.], 'ETH/USDT': [1., 1., 1., 4.]},
                          index=pd.date_range('2024-12-31', periods=4, tz='UTC'))
    return prices, dates


def test_known_six_portfolio_rational_posterior():
    prices, dates = inputs()
    weights = module.cover_allocation_weights(prices, dates, resolution=2)
    # Six experts earn 1,1,1,3/2,3/2,2. BTC weighted mass 7/2 over total8.
    np.testing.assert_allclose(weights.iloc[1], [7/16, 9/32, 9/32], atol=1e-15)
    np.testing.assert_allclose(weights.iloc[2], [17/50, 33/100, 33/100], atol=1e-15)


def test_initial_uniform_and_flat_prices_never_create_signal():
    prices, dates = inputs()
    prices[:] = 1.
    np.testing.assert_allclose(module.cover_allocation_weights(prices, dates), 1/3, atol=1e-14)
    assert module.triangular_portfolios().shape == (5151, 3)


def test_future_perturbation_and_actual_prefix_refit_exact():
    prices, dates = inputs()
    full = module.cover_allocation_weights(prices, dates)
    prefix = module.cover_allocation_weights(prices.iloc[:2], dates[:2])
    pd.testing.assert_frame_equal(full.iloc[:2], prefix)
    prices.iloc[2:] *= 100
    pd.testing.assert_frame_equal(full.iloc[:2], module.cover_allocation_weights(prices, dates).iloc[:2])


@pytest.mark.parametrize('fault', ['missing_price', 'zero', 'duplicate', 'decision_gap', 'wrong_assets'])
def test_reject_inadmissible_inputs(fault):
    prices, dates = inputs()
    if fault == 'missing_price': prices = prices.drop(prices.index[1])
    if fault == 'zero': prices.iloc[0, 0] = 0
    if fault == 'duplicate': prices = pd.concat([prices, prices.iloc[-1:]])
    if fault == 'decision_gap': dates = dates.delete(1)
    if fault == 'wrong_assets': prices = prices.iloc[:, ::-1]
    with pytest.raises(ValueError):
        module.cover_allocation_weights(prices, dates)
