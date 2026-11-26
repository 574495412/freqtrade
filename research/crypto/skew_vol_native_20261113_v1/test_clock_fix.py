# ruff: noqa: S101 - pytest assertions
import pandas as pd
import pytest

from research.crypto.skew_vol_native_20261113_v1.clock_fix import complete_initial_cash


def test_equal_boundary_keeps_exactly_the_actual_observation():
    start = pd.Timestamp("2025-01-01T00:00Z")
    frame = pd.DataFrame({"date": [start], "equity": [1000.0]})
    result = complete_initial_cash(frame, start, start, 1000)
    pd.testing.assert_frame_equal(frame, result)
    assert len(result) == 1


def test_padding_does_not_include_first_actual_clock():
    start = pd.Timestamp("2025-01-01T00:00Z")
    actual = pd.Timestamp("2025-01-01T04:00Z")
    frame = pd.DataFrame({"date": [actual], "equity": [990.0]})
    result = complete_initial_cash(frame, start, actual, 1000)
    assert result.date.tolist() == list(pd.date_range(start, actual, freq="h"))
    assert result.equity.tolist() == [1000, 1000, 1000, 1000, 990]


def test_actual_duplicates_fail_instead_of_deduplication():
    start = pd.Timestamp("2025-01-01T00:00Z")
    with pytest.raises(ValueError):
        complete_initial_cash(pd.DataFrame({"date": [start, start]}), start, start, 1000)
