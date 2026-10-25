"""Target dollar neutrality is separate from realized partial-fill neutrality."""

from copy import deepcopy

import pandas as pd
import pytest

from research.crypto.factor_portfolio import weights as original_weights
from research.crypto.factor_portfolio_refined import replay, weights
from tests.research.test_factor_portfolio import data


def test_unequal_volatility_preserves_both_target_side_budgets():
    score = {"A": 0.1, "B": 0.2, "C": -0.1, "D": -0.2}
    vol = {"A": 0.01, "B": 0.02, "C": 0.03, "D": 0.04}
    w = weights(score, vol, 0.4, 0.0016, 4, neutral=True)
    assert sum(w.values()) == pytest.approx(0)
    assert sum(abs(v) for v in w.values()) == pytest.approx(0.4)
    assert weights({"A": 0.1}, vol, 0.4, 0.0016, 4, neutral=True) == {}
    assert weights(score, vol, 0.4, 0.0016, 4) == original_weights(score, vol, 0.4, 0.0016, 4)


def test_target_neutral_does_not_hide_asymmetric_capacity_exposure():
    b, f, s, c = data()
    b["ETHUSDT"] = deepcopy(b["BTCUSDT"])
    f["ETHUSDT"] = f["BTCUSDT"].copy()
    s["ETHUSDT"] = -0.1
    s["vol_ETHUSDT"] = 0.01
    c.update(neutral_target=True, max_positions=2, gross=0.4)
    # The future fill volume differs, while the submitted targets stay balanced.
    b["ETHUSDT"].loc["2025-01-01T01:00Z":, "quote_volume"] = 1
    a = replay(b, f, s, c, "2025-01-01T01:00", "2025-01-01T02:00")
    assert a["curve"].net_exposure.abs().max() > 100
    last = a["curve"].iloc[-1]
    expected = sum(
        p["side"] * p["q"] * b[symbol].loc[pd.Timestamp("2025-01-01T01:55Z"), "close"]
        for symbol, p in a["positions"].items()
    )
    assert last.net_exposure == pytest.approx(expected)
