# ruff: noqa: S101 - pytest assertions are the checks
from types import SimpleNamespace

import pandas as pd
import pytest

from research.crypto.band_trend_native_20261113_v1.observe_clock import (
    attach,
    complete_initial_cash,
)


def test_observer_preserves_every_original_event_and_observes_before_it():
    event_times = [
        "2025-01-01T04:00Z",
        "2025-01-01T04:00Z",
        "2025-01-01T04:05Z",
        "2025-01-01T05:00Z",
        "2025-01-01T05:00Z",
        "2025-01-01T06:00Z",
    ]
    events = [(pd.Timestamp(t), "pair", i) for i, t in enumerate(event_times)]
    passed, observations = [], []
    account = SimpleNamespace(_time_pair_generator_det=lambda: iter(events))
    observer = SimpleNamespace(capture=lambda t: observations.append((t, len(passed))))
    attach(account, observer, "2025-01-01T00:00Z", "2025-01-01T05:00Z", 500)
    for event in account._time_pair_generator_det():
        passed.append(event)  # noqa: PERF402 - capture must observe incremental consumption
    assert passed == events
    assert observations == [(pd.Timestamp(event_times[0]), 0), (pd.Timestamp(event_times[3]), 3)]


def test_initial_cash_only_precedes_first_actual_generator_event():
    frame = pd.DataFrame({"date": [pd.Timestamp("2025-01-01T04:00Z")], "equity": [490.0]})
    result = complete_initial_cash(frame, "2025-01-01T00:00Z", frame.date.iloc[0], 500)
    assert len(result) == 5
    assert result.equity.tolist() == [500, 500, 500, 500, 490]
    with pytest.raises(ValueError):
        complete_initial_cash(result, "2025-01-01T00:00Z", frame.date.iloc[0], 500)
