"""Signal ranking and chronological screens must not use outcomes."""

import numpy as np
import pandas as pd

from research.crypto.screen_conditional_factors import choose_proposals, period_rows, summary


def test_proposal_selection_ignores_outcomes_and_breaks_ties_by_symbol():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-07-01T00:00Z"] * 4),
            "symbol": ["SOLUSDT", "BTCUSDT", "ETHUSDT", "BNBUSDT"],
            "side": ["long"] * 4,
            "predicted_win_probability": [0.9, 0.9, 0.79, 0.99],
            "predicted_net_return": [0.002, 0.002, 1.0, 0.0009],
            "net_return_label": [100.0, -100.0, 1000.0, 1000.0],
        }
    )
    plan = {
        "signal_selection": {
            "small_minimum_win_probability": 0.8,
            "minimum_predicted_net_return": 0.001,
        }
    }
    selected = choose_proposals(frame, plan, "small")
    assert selected.symbol.tolist() == ["BTCUSDT"]
    frame["net_return_label"] *= -1
    assert choose_proposals(
        frame.sample(frac=1, random_state=4), plan, "small"
    ).symbol.tolist() == ["BTCUSDT"]


def test_predictions_are_purged_by_full_horizon_without_observing_exit():
    frame = pd.DataFrame(
        {"date": pd.to_datetime(["2024-09-29T23:45Z", "2024-09-29T23:50Z", "2024-09-30T12:00Z"])}
    )
    assert period_rows(frame, "2024-07-01", "2024-10-01", 288).tolist() == [True, False, False]


def test_no_signal_has_no_measured_winning_rate_or_profit_claim():
    result = summary(pd.DataFrame())
    assert result["signals"] == 0
    assert result["actual_net_win_rate"] is None
    assert result["mean_unit_net_label"] is None


def test_overlap_is_reported_as_labels_never_summed_to_account_profit():
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-07-01", periods=10, freq="5min", tz="UTC"),
            "net_return_label": np.full(10, 0.01),
            "net_win_label": np.ones(10),
            "predicted_win_probability": np.full(10, 0.9),
            "predicted_net_return": np.full(10, 0.005),
        }
    )
    result = summary(frame)
    assert np.isclose(result["mean_unit_net_label"], 0.01)
    assert result["overlapping_labels_are_not_account_pnl"]
    assert "account_return" not in result
